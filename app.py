
import os
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, render_template, request, jsonify, send_file

import PyPDF2

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
  refs: array of integers
plan: array of items, each item:
  number: integer
  title: string
  bullets: array of short strings
  aligned_dx_numbers: array of integers
  refs: array of integers
pubmed: array of 3 to 12 items, each item:
  number: integer
  citation: string
  pmid: string
warnings: array of short strings
raw_excerpt: string

Rules:
1 Use only facts supported by the note. If unknown, leave empty.
2 Do not invent demographics. patient_block must only include what is present, formatted as a clean header block.
3 diagnoses must be problem list style, include laterality and severity when present.
4 plan bullets must be actionable, conservative, evidence based, and aligned to diagnoses.
5 refs are citation numbers that point to pubmed items.
6 pubmed citation should look like a proper bibliography line. If not sure about PMID, leave it empty.
7 raw_excerpt must be the first 1200 characters of the note.

Encounter note:
{excerpt}
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

Special requests:
special_requests is an intent signal. Never quote it verbatim. Never paste it. Use it indirectly and naturally.

Structure rules for letter_plain:
1 Start with patient_block exactly as provided, then a blank line.
2 Use headings and short paragraphs with good spacing.
3 Include a one line Purpose statement using letter_type and reason_for_referral.
4 Include sections: Clinical summary, Assessment, Plan, Evidence, Closing, Disclaimer.
5 In Assessment and Plan, reference evidence with bracket numbers like [1] that point to the Evidence section.
6 Evidence section must list pubmed items in order as: [n] citation. Include PMID if present.

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
    obj, err = llm_json(analyze_prompt(text))
    if err:
        return jsonify({"ok": False, "error": err}), 200

    data = dict(ANALYZE_SCHEMA)
    data.update(obj or {})
    data["raw_excerpt"] = clamp_text(text, 1200)

    pub = data.get("pubmed") or []
    if isinstance(pub, list):
        for i, item in enumerate(pub, start=1):
            if isinstance(item, dict) and "number" not in item:
                item["number"] = i

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
