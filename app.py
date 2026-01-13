
import os
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, render_template, request, jsonify, send_file

import PyPDF2

import httpx

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from reportlab.lib.pagesizes import letter as rl_letter
    from reportlab.pdfgen import canvas
except Exception:
    canvas = None
    rl_letter = None

APP_VERSION = os.getenv("APP_VERSION", "2026.2")

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def extract_pdf_text(file_storage) -> str:
    reader = PyPDF2.PdfReader(file_storage)
    parts: List[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()

def client_ready() -> Tuple[bool, str]:
    if OpenAI is None:
        return False, "OpenAI SDK not installed"
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return False, "OPENAI_API_KEY is missing"
    return True, ""

def get_client():
    ok, _ = client_ready()
    if not ok:
        return None
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY").strip())

def model_name() -> str:
    return (os.getenv("OPENAI_MODEL", "").strip() or "gpt-4.1")

def clamp_text(s: str, limit: int) -> str:
    return (s or "")[:limit]

def safe_json_loads(s: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not s:
        return None, "Empty model output"
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj, ""
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj, ""
        except Exception:
            pass
    return None, "Model did not return valid json"

def llm_json(prompt: str) -> Tuple[Optional[Dict[str, Any]], str]:
    client = get_client()
    if client is None:
        ok, msg = client_ready()
        return None, msg or "Client not available"
    try:
        res = client.chat.completions.create(
            model=model_name(),
            messages=[
                {"role": "system", "content": "Return strict JSON only. No markdown. No extra text."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        text = (res.choices[0].message.content or "").strip()
        obj, err = safe_json_loads(text)
        if err:
            return None, err
        return obj, ""
    except Exception as e:
        return None, f"LLM request failed: {type(e).__name__}: {e}"

ANALYZE_SCHEMA: Dict[str, Any] = {
    "provider_name": "",
    "patient_block": "",
    "diagnoses": [],
    "plan": [],
    "pubmed": [],
    "warnings": [],
    "raw_excerpt": ""
}


def analyze_prompt(note_text: str) -> str:
    excerpt = clamp_text(note_text, 16000)
    return f"""
You are a clinician assistant. You are given an encounter note extracted from a PDF.

Output VALID JSON only, matching this schema exactly:
provider_name: string
patient_block: string
diagnoses: array of items, each item:
  number: integer
  code: string
  label: string
  bullets: array of short strings
plan: array of items, each item:
  number: integer
  title: string
  bullets: array of short strings
  aligned_dx_numbers: array of integers
warnings: array of short strings
raw_excerpt: string

Rules:
1 Use only facts supported by the note. If unknown, leave empty.
2 Do not invent demographics. patient_block must only include what is present, formatted as a clean header block.
3 diagnoses must be problem list style, include laterality and severity when present.
4 plan bullets must be actionable, conservative, and specific.
5 raw_excerpt must be the first 1200 characters of the note.

Encounter note:
{excerpt}
""".strip()





def pubmed_search(queries: List[str], max_items: int = 8, timeout_s: float = 8.0) -> List[Dict[str, Any]]:
    """
    Lightweight PubMed retrieval via NCBI E-utilities.
    Returns a numbered bibliography list. No PHI is sent.
    """
    q = " OR ".join([q for q in queries if q.strip()][:4]).strip()
    if not q:
        return []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            esearch = client.get(
                base + "esearch.fcgi",
                params={"db": "pubmed", "term": q, "retmode": "json", "retmax": str(max_items)},
            )
            esearch.raise_for_status()
            ids = (esearch.json().get("esearchresult", {}).get("idlist") or [])[:max_items]
            if not ids:
                return []
            esummary = client.get(
                base + "esummary.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
            )
            esummary.raise_for_status()
            result = esummary.json().get("result", {})
            out: List[Dict[str, Any]] = []
            n = 1
            for pid in ids:
                item = result.get(pid, {})
                if not isinstance(item, dict):
                    continue
                title = (item.get("title") or "").strip().rstrip(".")
                source = (item.get("source") or "").strip()
                pubdate = (item.get("pubdate") or "").strip()
                authors = item.get("authors") or []
                author_text = ""
                if isinstance(authors, list) and authors:
                    names = [a.get("name","").strip() for a in authors if isinstance(a, dict)]
                    names = [x for x in names if x]
                    author_text = ", ".join(names[:3])
                    if len(names) > 3:
                        author_text += " et al"
                parts = [p for p in [author_text, title, source, pubdate] if p]
                citation = ". ".join(parts) + "."
                out.append({"number": n, "citation": citation, "pmid": str(pid)})
                n += 1
            return out
    except Exception:
        return []

def refs_enrich_prompt(analysis_core: Dict[str, Any], pubmed: List[Dict[str, Any]]) -> str:
    return f"""
You are a clinician assistant.

You will assign evidence reference numbers to diagnoses and plan items using ONLY the provided PubMed bibliography.
Do not change wording of bullets unless required for clarity. Do not invent diagnoses or plan items.

Return VALID JSON only matching this schema:
diagnoses: array of items:
  number: integer
  code: string
  label: string
  bullets: array of short strings
  refs: array of integers
plan: array of items:
  number: integer
  title: string
  bullets: array of short strings
  aligned_dx_numbers: array of integers
  refs: array of integers

Rules:
1 refs values must be bibliography numbers. Use 1 to {len(pubmed)}.
2 If no citation fits, leave refs empty.
3 Try to assign at least one reference per diagnosis and per plan item when possible.
4 Keep refs short, no more than 3 per item.

Analysis core:
{json.dumps(analysis_core, ensure_ascii=False)}

PubMed bibliography:
{json.dumps(pubmed, ensure_ascii=False)}
""".strip()
def letter_prompt(note_text: str, form: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    excerpt = clamp_text(note_text, 16000)

    return f"""
You are a clinician assistant. Create an Output Communication letter.

Output VALID JSON only with this schema:
letter_plain: string
letter_html: string

Tone rules:
If recipient_type equals "Patient", write in patient friendly accessible language while staying professional.
Otherwise write in technical physician style that is precise and concise.

Special requests handling:
special_requests is an intent signal. Never quote it. Never repeat it. Never refer to it as "special requests".
Do not copy any phrase from it verbatim, even short phrases. Use it only to adjust emphasis, framing, and closing language.
If the intent suggests building trust, use respectful, confidence building language and clear next steps.
If the intent suggests collaboration or future referrals, weave it subtly into the professional closing without sounding salesy.

Structure rules for letter_plain:
1 Start with patient_block exactly as provided, then a blank line.
2 Use headings and short paragraphs with good spacing.
3 Include a one line Purpose statement using letter_type and reason_for_referral.
4 Include sections: Clinical summary, Assessment, Plan, Evidence, Closing, Disclaimer.
5 In Assessment and Plan, reference evidence with bracket numbers like [1] that point to the Evidence section.
6 Evidence section must list pubmed items in order as: [n] citation. Include PMID if present.
7 Never invent facts. If something is not in the note, omit it.

Structure rules for letter_html:
Provide the same content as clean HTML with headings and paragraphs.
Use <h3> for headings, <p> for paragraphs, and <br> only inside the patient block.

Form:
{json.dumps(form, ensure_ascii=False)}

Analysis:
{json.dumps(analysis, ensure_ascii=False)}

Encounter note:
{excerpt}
""".strip()


@app.get("/")
def index():
    return render_template("index.html", version=APP_VERSION)

@app.post("/analyze")
def analyze():
    file = request.files.get("pdf")
    if not file:
        return jsonify({"ok": False, "error": "No PDF uploaded"}), 400

    text = extract_pdf_text(file)

    core_obj, err = llm_json(analyze_prompt(text))
    if err:
        return jsonify({"ok": False, "error": err}), 200

    core = {
        "provider_name": (core_obj or {}).get("provider_name", "") or "",
        "patient_block": (core_obj or {}).get("patient_block", "") or "",
        "diagnoses": (core_obj or {}).get("diagnoses", []) or [],
        "plan": (core_obj or {}).get("plan", []) or [],
        "warnings": (core_obj or {}).get("warnings", []) or [],
        "raw_excerpt": clamp_text(text, 1200),
    }

    dx_list = core.get("diagnoses") or []
    queries: List[str] = []
    if isinstance(dx_list, list):
        for d in dx_list[:4]:
            if isinstance(d, dict):
                q = (d.get("label") or "").strip()
                if q:
                    queries.append(q)

    pubmed = pubmed_search(queries, max_items=8)

    enrich_obj, enrich_err = llm_json(refs_enrich_prompt(core, pubmed))
    diagnoses = core.get("diagnoses") or []
    plan = core.get("plan") or []
    if not enrich_err and enrich_obj:
        diagnoses = enrich_obj.get("diagnoses") or diagnoses
        plan = enrich_obj.get("plan") or plan

    data = dict(ANALYZE_SCHEMA)
    data.update({
        "provider_name": core.get("provider_name", ""),
        "patient_block": core.get("patient_block", ""),
        "diagnoses": diagnoses,
        "plan": plan,
        "pubmed": pubmed,
        "warnings": core.get("warnings", []),
        "raw_excerpt": core.get("raw_excerpt", ""),
    })

    return jsonify({"ok": True, "data": data}), 200

@app.post("/generate")
def generate():
    file = request.files.get("pdf")
    payload_text = request.form.get("payload", "{}")
    try:
        payload = json.loads(payload_text)
    except Exception:
        payload = {}

    if not file:
        return jsonify({"ok": False, "error": "No PDF uploaded"}), 400

    form = payload.get("form", {}) or {}
    analysis = payload.get("analysis", {}) or {}

    note_text = extract_pdf_text(file)
    obj, err = llm_json(letter_prompt(note_text, form, analysis))
    if err:
        return jsonify({"ok": False, "error": err}), 200

    letter_plain = (obj or {}).get("letter_plain", "") or ""
    letter_html = (obj or {}).get("letter_html", "") or ""

    if not letter_plain.strip():
        return jsonify({"ok": False, "error": "Empty output generated"}), 200

    return jsonify({"ok": True, "letter_plain": letter_plain.strip(), "letter_html": letter_html.strip()}), 200

@app.post("/export_pdf")
def export_pdf():
    if canvas is None or rl_letter is None:
        return jsonify({"ok": False, "error": "PDF export not available"}), 500

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text to export"}), 400

    out_path = "/tmp/ai4health_output.pdf"
    c = canvas.Canvas(out_path, pagesize=rl_letter)
    width, height = rl_letter

    left = 54
    top = height - 54
    line_height = 12
    y = top

    c.setFont("Times-Roman", 11)

    def draw_wrapped(line: str):
        nonlocal y
        max_chars = 95
        line = line.rstrip()
        if not line:
            y -= line_height
            return
        while len(line) > max_chars:
            c.drawString(left, y, line[:max_chars])
            line = line[max_chars:]
            y -= line_height
            if y < 72:
                c.showPage()
                c.setFont("Times-Roman", 11)
                y = top
        c.drawString(left, y, line)
        y -= line_height

    for raw_line in text.splitlines():
        if y < 72:
            c.showPage()
            c.setFont("Times-Roman", 11)
            y = top
        draw_wrapped(raw_line)

    c.save()
    return send_file(out_path, as_attachment=True, download_name="ai4health_output.pdf", mimetype="application/pdf")

@app.get("/healthz")
def healthz():
    ok, msg = client_ready()
    return jsonify({
        "ok": True,
        "app_version": APP_VERSION,
        "time_utc": now_utc_iso(),
        "openai_ready": ok,
        "openai_message": msg,
        "model": model_name(),
    }), 200

if __name__ == "__main__":
    app.run(debug=True)
