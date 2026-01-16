
import os
import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import PyPDF2
import requests
from flask import Flask, jsonify, render_template, request, send_file

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from reportlab.lib.pagesizes import letter as rl_letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
except Exception:
    canvas = None
    rl_letter = None
    ImageReader = None

APP_VERSION = os.getenv("APP_VERSION", "2026.4")

app = Flask(__name__, template_folder="templates", static_folder="static", static_url_path="/static")

# In memory job store
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def clamp_text(s: str, limit: int) -> str:
    return (s or "")[:limit]

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

def model_name() -> str:
    return (os.getenv("OPENAI_MODEL", "").strip() or "gpt-4.1")

def get_client():
    ok, _ = client_ready()
    if not ok:
        return None
    key = os.getenv("OPENAI_API_KEY").strip()
    # Set a sane timeout to avoid hanging requests
    return OpenAI(api_key=key, timeout=60)

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

def llm_json(prompt: str, temperature: float = 0.2) -> Tuple[Optional[Dict[str, Any]], str]:
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
            temperature=temperature,
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
    "summary_html": "",
    "diagnoses": [],
    "plan": [],
    "references": [],
    "warnings": [],
}

def analyze_prompt(note_text: str) -> str:
    excerpt = clamp_text(note_text, 16000)
    return f"""
You are a clinician assistant. You are given an encounter note extracted from a PDF.

Output VALID JSON only, matching this schema exactly:
provider_name: string
patient_block: string
summary_html: string
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

Rules:
1 Use only facts supported by the note. If unknown, leave empty.
2 patient_block must contain patient demographics only. Include PHN if present. Exclude provider address and clinic address. Use <br> line breaks.
3 summary_html should be a clean summary section with headings and paragraphs. Use <b> for headings and <p> blocks. No markdown.
4 diagnoses must be problem list style, include laterality and severity when present.
5 plan bullets must be actionable, conservative, and aligned to diagnoses.
6 If exam findings are present, include them in summary_html with clear headings such as Exam findings and Imaging when applicable.


Encounter note:
{excerpt}
""".strip()

def pubmed_fetch_for_terms(terms: List[str], max_items: int = 12) -> List[Dict[str, str]]:
    # NCBI E utilities. Keep it lightweight, avoid rate limits
    uniq_terms = []
    for t in terms:
        t = (t or "").strip()
        if t and t.lower() not in [x.lower() for x in uniq_terms]:
            uniq_terms.append(t)
    if not uniq_terms:
        # Fallback to a broad ophthalmology evidence search to ensure we can always
        # return at least one PubMed reference for the case.
        uniq_terms = ["ophthalmology clinical practice guideline"]

    pmids: List[str] = []
    for term in uniq_terms[:6]:
        q = f"{term} ophthalmology"
        try:
            r = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pubmed", "term": q, "retmax": 3, "retmode": "json"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            ids = (data.get("esearchresult") or {}).get("idlist") or []
            for pid in ids:
                if pid not in pmids:
                    pmids.append(pid)
            if len(pmids) >= max_items:
                break
        except Exception:
            continue

    if not pmids:
        # Final fallback query to guarantee at least one reference.
        try:
            r = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pubmed", "term": "ophthalmology review", "retmax": 3, "retmode": "json"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            ids = (data.get("esearchresult") or {}).get("idlist") or []
            for pid in ids:
                if pid not in pmids:
                    pmids.append(pid)
        except Exception:
            pass
    if not pmids:
        return []

    pmids = pmids[:max_items]
    try:
        r = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("result") or {}
        out: List[Dict[str, str]] = []
        for i, pid in enumerate(pmids, start=1):
            item = result.get(pid) or {}
            title = (item.get("title") or "").strip().rstrip(".")
            source = (item.get("source") or "").strip()
            pubdate = (item.get("pubdate") or "").strip()
            authors = item.get("authors") or []
            first_author = (authors[0].get("name") if authors else "") or ""
            citation = " ".join([x for x in [first_author, title, source, pubdate] if x]).strip()
            out.append({"number": str(i), "pmid": pid, "citation": citation})
        return out
    except Exception:
        return []

def assign_citations_prompt(analysis: Dict[str, Any]) -> str:
    # Ask model to add refs based on the fetched reference list
    return f"""
You are a clinician assistant. You are given an analysis object and a numbered reference list.
Assign appropriate reference numbers to each diagnosis and plan item.

Output VALID JSON only with this schema:
diagnoses: array of items, each item:
  number: integer
  refs: array of integers
plan: array of items, each item:
  number: integer
  refs: array of integers

Rules:
1 Use only reference numbers that exist in references.
2 Prefer 1 to 3 refs per item.
3 There must always be at least one reference number used somewhere in diagnoses or plan.
4 If a direct match is unclear, choose the most relevant general reference for the condition or specialty area to provide evidence context.

Analysis:
{json.dumps(analysis, ensure_ascii=False)}
""".strip()

def letter_prompt(form: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    return f"""
You are a clinician assistant. Create an Output Communication report.

Output VALID JSON only with this schema:
letter_plain: string
letter_html: string

Tone rules:
If recipient_type equals "Patient", write in patient friendly accessible language while staying professional.
Otherwise write in technical physician style that is precise and concise.

Special requests:
special_requests is an intent signal. Never quote it verbatim. Never paste it. Use it indirectly and naturally.

Clinic context:
clinic_name: {os.getenv("CLINIC_NAME","")}
clinic_address: {os.getenv("CLINIC_ADDRESS","")}
clinic_phone: {os.getenv("CLINIC_PHONE","")}

Structure:
Create a professional referral or report letter.

Letterhead rules:
1 Use clinic_name, clinic_address, clinic_phone when present. If missing, omit that line.
2 Include the current date.
3 Include To and From lines.
4 Include Reason for referral immediately under the letterhead.
5 Then include patient_block exactly as provided.
6 Start the letter_plain with header_block exactly as provided in Form, then a blank line, then patient_block, then a blank line, then the body sections.
7 For letter_html, render header_block using <div> and <p> tags, then patient_block, then sections.

Body rules:
1 Use short paragraphs with good spacing.
2 Include sections: Clinical summary, Exam findings, Assessment, Plan.
3 Include exam findings with more granularity when available in the note. Prefer objective measurements, key negatives, imaging summaries, and relevant test results.
4 Do not include Evidence or Disclaimer sections in the letter.
5 Do not include citations or bracket numbers in the letter body.
6 End with Kind regards and the authoring doctor name. Do not add a section heading for this sign off.

Form:
{json.dumps(form, ensure_ascii=False)}

Analysis:
{json.dumps(analysis, ensure_ascii=False)}
""".strip()

def new_job_id() -> str:
    return f"job_{int(time.time() * 1000)}_{os.urandom(4).hex()}"

def set_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id) or {}
        job.update(updates)
        JOBS[job_id] = job

def get_job(job_id: str) -> Dict[str, Any]:
    with JOBS_LOCK:
        return dict(JOBS.get(job_id) or {})

def run_analysis_job(job_id: str, note_text: str) -> None:
    set_job(job_id, status="processing", updated_at=now_utc_iso())
    obj, err = llm_json(analyze_prompt(note_text))
    if err or not obj:
        set_job(job_id, status="error", error=err or "Analysis failed", updated_at=now_utc_iso())
        return

    analysis = dict(ANALYZE_SCHEMA)
    analysis.update(obj)

    # Fetch PubMed references based on diagnoses
    terms = []
    for dx in analysis.get("diagnoses") or []:
        if isinstance(dx, dict):
            label = (dx.get("label") or "").strip()
            if label:
                terms.append(label)
    references = pubmed_fetch_for_terms(terms)
    analysis["references"] = references

    # Assign citation numbers
    if references:
        cites_obj, cites_err = llm_json(assign_citations_prompt(analysis), temperature=0.0)
        if not cites_err and cites_obj:
            dx_map = {int(x.get("number")): x.get("refs") for x in (cites_obj.get("diagnoses") or []) if isinstance(x, dict) and str(x.get("number", "")).isdigit()}
            pl_map = {int(x.get("number")): x.get("refs") for x in (cites_obj.get("plan") or []) if isinstance(x, dict) and str(x.get("number", "")).isdigit()}
            for dx in analysis.get("diagnoses") or []:
                if isinstance(dx, dict) and isinstance(dx.get("number"), int):
                    dx["refs"] = dx_map.get(dx["number"], [])
            for pl in analysis.get("plan") or []:
                if isinstance(pl, dict) and isinstance(pl.get("number"), int):
                    pl["refs"] = pl_map.get(pl["number"], [])

    # Guarantee at least one reference number is used somewhere when references exist
    if analysis.get("references"):
        used = False
        for dx in analysis.get("diagnoses") or []:
            if isinstance(dx, dict) and dx.get("refs"):
                used = True
                break
        if not used:
            for pl in analysis.get("plan") or []:
                if isinstance(pl, dict) and pl.get("refs"):
                    used = True
                    break
        if not used:
            # Attach reference 1 as a general evidence context anchor
            if isinstance(analysis.get("diagnoses"), list) and analysis["diagnoses"]:
                if isinstance(analysis["diagnoses"][0], dict):
                    analysis["diagnoses"][0]["refs"] = [1]
            if isinstance(analysis.get("plan"), list) and analysis["plan"]:
                if isinstance(analysis["plan"][0], dict):
                    analysis["plan"][0]["refs"] = [1]

    set_job(job_id, status="complete", data=analysis, updated_at=now_utc_iso())

@app.get("/")
def index():
    return render_template("index.html", version=APP_VERSION)

@app.post("/analyze_start")
def analyze_start():
    file = request.files.get("pdf")
    if not file:
        return jsonify({"ok": False, "error": "No PDF uploaded"}), 400

    note_text = extract_pdf_text(file)
    if not note_text:
        return jsonify({"ok": False, "error": "No text extracted from PDF"}), 200

    job_id = new_job_id()
    set_job(job_id, status="waiting", updated_at=now_utc_iso())

    t = threading.Thread(target=run_analysis_job, args=(job_id, note_text), daemon=True)
    t.start()

    return jsonify({"ok": True, "job_id": job_id}), 200

@app.get("/analyze_status")
def analyze_status():
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"ok": False, "error": "Missing job_id"}), 400
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Unknown job_id"}), 404
    return jsonify({"ok": True, **job}), 200

@app.post("/generate_report")
def generate_report():
    payload = request.get_json(silent=True) or {}
    form = payload.get("form") or {}
    analysis = payload.get("analysis") or {}

    obj, err = llm_json(letter_prompt(form, analysis))
    if err or not obj:
        return jsonify({"ok": False, "error": err or "Generation failed"}), 200

    letter_plain = (obj.get("letter_plain") or "").strip()
    letter_html = (obj.get("letter_html") or "").strip()
    # Some model outputs may leak html breaks into the plain text. Normalize.
    if letter_plain:
        letter_plain = re.sub(r"<\s*br\s*/?\s*>", "\n", letter_plain, flags=re.IGNORECASE)
        letter_plain = re.sub(r"<\s*/?p\s*>", "\n", letter_plain, flags=re.IGNORECASE)
        letter_plain = re.sub(r"<[^>]+>", "", letter_plain)
        letter_plain = re.sub(r"\n{3,}", "\n\n", letter_plain).strip()
    if not letter_plain:
        return jsonify({"ok": False, "error": "Empty output"}), 200

    return jsonify({"ok": True, "letter_plain": letter_plain, "letter_html": letter_html}), 200

@app.post("/export_pdf")

def signature_slug(provider_name: str) -> str:
    s = (provider_name or "").strip().lower()
    s = re.sub(r"\b(dr\.?|md|od|mba)\b", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

def find_signature_image(provider_name: str) -> Optional[str]:
    base_dir = os.getenv("SIGNATURE_DIR", "static/signatures")
    abs_dir = os.path.join(os.path.dirname(__file__), base_dir)
    slug = signature_slug(provider_name)
    if not slug:
        return None
    for ext in (".png", ".jpg", ".jpeg"):
        cand = os.path.join(abs_dir, slug + ext)
        if os.path.exists(cand):
            return cand
    return None

def export_pdf():
    if canvas is None or rl_letter is None:
        return jsonify({"ok": False, "error": "PDF export not available"}), 500

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    provider_name = (payload.get("provider_name") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text to export"}), 400

    out_path = "/tmp/ai4health_output.pdf"
    c = canvas.Canvas(out_path, pagesize=rl_letter)
    _, height = rl_letter

    left = 54
    top = height - 54
    line_height = 13
    # Optional letterhead image at top of each page
    # Place a PNG at static/letterhead.png (or set LETTERHEAD_IMAGE) to enable
    letterhead_path = os.getenv("LETTERHEAD_IMAGE", "static/letterhead.png")
    abs_letterhead = os.path.join(os.path.dirname(__file__), letterhead_path)

    def draw_letterhead_and_get_start_y() -> float:
        if ImageReader is None:
            return top
        try:
            if not os.path.exists(abs_letterhead):
                return top
            img = ImageReader(abs_letterhead)
            iw, ih = img.getSize()
            target_w = 504  # 7 inches at 72 dpi
            scale = target_w / float(iw) if iw else 1.0
            target_h = float(ih) * scale
            c.drawImage(img, left, top - target_h + 12, width=target_w, height=target_h, mask="auto")
            return top - target_h - 6
        except Exception:
            return top

    y = draw_letterhead_and_get_start_y()

    signature_path = find_signature_image(provider_name) if provider_name else None

    c.setFont("Times-Roman", 12)

    def draw_wrapped(line: str):
        nonlocal y
        max_chars = 92
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
                c.setFont("Times-Roman", 12)
                y = draw_letterhead_and_get_start_y()
        c.drawString(left, y, line)
        y -= line_height

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = (raw_line or "").rstrip()
        if y < 72:
            c.showPage()
            c.setFont("Times-Roman", 12)
            y = draw_letterhead_and_get_start_y()
        # Signature handling
        if line.strip().lower() in ("kind regards,", "kind regards", "regards,", "regards"):
            draw_wrapped(raw_line)
            if signature_path and ImageReader is not None:
                try:
                    img = ImageReader(signature_path)
                    iw, ih = img.getSize()
                    target_w = 320
                    scale = target_w / float(iw) if iw else 1.0
                    target_h = float(ih) * scale
                    if y - target_h < 72:
                        c.showPage()
                        c.setFont("Times-Roman", 12)
                        y = draw_letterhead_and_get_start_y()
                    c.drawImage(img, left, y - target_h + 6, width=target_w, height=target_h, mask="auto")
                    y -= (target_h + 8)
                    # Skip next non empty provider line
                    j = i + 1
                    while j < len(lines) and not (lines[j] or "").strip():
                        j += 1
                    if j < len(lines):
                        nxt = (lines[j] or "").strip().lower()
                        prov = (provider_name or "").strip().lower()
                        if prov and (prov in nxt or nxt.startswith("dr")):
                            i = j + 1
                            continue
                except Exception:
                    pass
            else:
                # If the next line is empty, inject provider name
                if provider_name:
                    j = i + 1
                    while j < len(lines) and not (lines[j] or "").strip():
                        j += 1
                    if j >= len(lines):
                        draw_wrapped(provider_name)
            i += 1
            continue
        draw_wrapped(raw_line)
        i += 1

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