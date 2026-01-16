
import os
import tempfile
import uuid
import json
import re
import html as html_lib
import threading
import time
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import PyPDF2
import requests
from flask import Flask, jsonify, render_template, request, send_file
from email.message import EmailMessage
import smtplib

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    from PIL import Image
except Exception:
    Image = None

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from reportlab.lib.pagesizes import letter as rl_letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_JUSTIFY
    from reportlab.lib import colors
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

def text_is_meaningful(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 250:
        return False
    alpha = sum(1 for ch in s if ch.isalpha())
    ratio = alpha / max(len(s), 1)
    return ratio >= 0.25

def ocr_pdf_bytes(pdf_bytes: bytes, max_pages: int = 12) -> Tuple[str, str]:
    if fitz is None or Image is None or pytesseract is None:
        return "", "OCR dependencies missing"
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return "", f"Could not open PDF for OCR: {e}"
    parts: List[str] = []
    try:
        pages = min(len(doc), max_pages)
        for i in range(pages):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=220)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            parts.append(pytesseract.image_to_string(img) or "")
    except Exception as e:
        return "", f"OCR failed: {e}"
    return "\n".join(parts).strip(), ""

def extract_text_from_upload(file_storage, force_ocr: bool) -> Tuple[str, bool, bool, str]:
    """Returns text, used_ocr, needs_ocr, error"""
    filename = (getattr(file_storage, "filename", "") or "").lower()
    data = file_storage.read()
    file_storage.stream.seek(0)

    extracted = ""
    if filename.endswith(".pdf"):
        try:
            extracted = extract_pdf_text(io.BytesIO(data))
        except Exception:
            extracted = ""

        if text_is_meaningful(extracted) and not force_ocr:
            return extracted, False, False, ""

        if not force_ocr:
            return extracted, False, True, ""

        ocr_text, err = ocr_pdf_bytes(data)
        if err:
            return extracted, False, True, err
        best = ocr_text if text_is_meaningful(ocr_text) or len(ocr_text) > len(extracted) else extracted
        return best, True, False, ""

    # Image uploads
    if force_ocr:
        if Image is None or pytesseract is None:
            return "", False, True, "OCR dependencies missing"
        try:
            img = Image.open(io.BytesIO(data))
            text = pytesseract.image_to_string(img) or ""
            return text.strip(), True, False, ""
        except Exception as e:
            return "", False, True, f"OCR failed: {e}"

    return "", False, True, "Unsupported file type"

def is_meaningful_text(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 250:
        return False
    letters = sum(1 for c in t if c.isalpha())
    ratio = letters / max(1, len(t))
    return ratio >= 0.25

def ocr_ready() -> Tuple[bool, str]:
    if fitz is None:
        return False, "PyMuPDF not available"
    if Image is None:
        return False, "Pillow not available"
    if pytesseract is None:
        return False, "pytesseract not available"
    return True, ""

def ocr_pdf_bytes(pdf_bytes: bytes, max_pages: int = 12) -> str:
    ok, _ = ocr_ready()
    if not ok:
        return ""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return ""
    parts: List[str] = []
    try:
        pages = min(len(doc), max_pages)
        for i in range(pages):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=220)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            txt = pytesseract.image_to_string(img) or ""
            parts.append(txt)
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return "\n".join(parts).strip()

def extract_text_with_ocr_gate(file_storage, force_ocr: bool) -> Tuple[str, bool, bool, str]:
    """
    Returns: text, used_ocr, needs_ocr, error
    """
    try:
        pdf_bytes = file_storage.read()
    except Exception:
        return "", False, False, "Unable to read uploaded file"
    # Reset stream for any later reads
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass

    extracted = ""
    try:
        extracted = extract_pdf_text(io.BytesIO(pdf_bytes))
    except Exception:
        extracted = ""

    if is_meaningful_text(extracted) and not force_ocr:
        return extracted, False, False, ""

    # If not meaningful and OCR not requested, ask for OCR
    if (not is_meaningful_text(extracted)) and (not force_ocr):
        return "", False, True, "No readable text extracted"

    # OCR path
    ok, msg = ocr_ready()
    if not ok:
        return "", False, False, f"OCR not available: {msg}"
    ocr_text = ocr_pdf_bytes(pdf_bytes)
    if is_meaningful_text(ocr_text):
        return ocr_text, True, False, ""
    # Fall back to whatever we extracted
    if extracted.strip():
        return extracted, True, False, ""
    return "", True, False, "OCR produced no readable text"

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

Required top section for letter_plain:
Use this exact order, one item per line.
To: <recipient>
From: <authoring provider>
Date: <current date>
<blank line>
Patient: <full name> | DOB: <date> (<age>) | Sex: <sex> | PHN: <phn>
Phone: <phone> | Email: <email if present> | Address: <address if present>
<blank line>
Reason for Referral: <diagnosis chosen plus reason_detail if provided>
<blank line>
Then the salutation line.

For letter_html, render the same information using <p> blocks and preserve blank lines using spacing.

Body rules:
1 The first body paragraph must be a referral narrative, not a section label. Start with: Thank you for seeing <patient>, a <age> year old <sex> patient who presented with <chief complaint> and is being referred for <reason plus requested service>.
2 The second sentence must add brief context and urgency if relevant.
3 After the opening narrative, use these headings exactly, each on its own line: Exam findings, Assessment, Plan.
4 Use short paragraphs and compact lists when helpful. Prefer objective measurements, key negatives, imaging summaries, and relevant test results.
5 Do not include Evidence or Disclaimer sections.
6 Do not include citations or bracket numbers in the letter body.
7 The closing paragraph must always include all three elements, written naturally:
  a A thank you and appreciation for seeing the patient
  b A subtle comanagement and collaboration signal, for example: I truly value comanaging patients with you and look forward to collaborating on future shared cases.
  c A request for their impressions and recommendations, asking for a reply
8 End with Kind regards and the authoring doctor name. Do not add a section heading for this sign off.

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

    handwritten = (request.form.get("handwritten") or "").strip().lower() in ("1", "true", "yes", "on")

    pdf_bytes = file.read()
    file.stream = io.BytesIO(pdf_bytes)
    note_text = extract_pdf_text(file.stream)

    if not handwritten and not text_is_meaningful(note_text):
        return jsonify({
            "ok": False,
            "needs_ocr": True,
            "error": "No readable text extracted. If these notes are scanned or handwritten, enable the handwritten option and run OCR."
        }), 200

    if handwritten:
        ocr_text, ocr_err = ocr_pdf_bytes(pdf_bytes)
        if ocr_text:
            note_text = ocr_text
        else:
            return jsonify({"ok": False, "error": ocr_err or "OCR did not return readable text"}), 200

    if not note_text:
        return jsonify({"ok": False, "error": "No text extracted"}), 200

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

    # Normalize patient block to avoid html line breaks leaking into the letter
    pb_html = (analysis.get("patient_block") or "")
    pb_plain = re.sub(r"<\s*br\s*/?\s*>", "\n", pb_html, flags=re.IGNORECASE)
    pb_plain = re.sub(r"<[^>]+>", "", pb_plain)
    pb_plain = re.sub(r"\n{3,}", "\n\n", pb_plain).strip()
    analysis["patient_block_plain"] = pb_plain

    # Helper fields used by the prompt
    form = dict(form) if isinstance(form, dict) else {}
    form.setdefault("current_date", datetime.now().strftime("%B %d, %Y"))
    rf = (form.get("reason_for_referral") or "").strip()
    rd = (form.get("reason_detail") or "").strip()
    if rf and rd:
        form["reason_for_referral_combined"] = f"{rf}, {rd}"
    else:
        form["reason_for_referral_combined"] = rf or rd

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

def signature_image_for_provider(provider_name: str) -> Optional[str]:
    """Backward compatible helper used by PDF export."""
    return find_signature_image(provider_name)

def html_to_text_for_pdf(html_in: str) -> str:
    s = (html_in or "").strip()
    if not s:
        return ""
    s = re.sub(r"(?is)<script.*?>.*?</script>", "", s)
    s = re.sub(r"(?is)<style.*?>.*?</style>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p\s*>", "\n\n", s)
    s = re.sub(r"(?i)<p\b[^>]*>", "", s)
    s = re.sub(r"(?i)</h[1-6]\s*>", "\n\n", s)
    s = re.sub(r"(?i)<h[1-6]\b[^>]*>", "", s)
    s = re.sub(r"(?i)<li\b[^>]*>", "* ", s)
    s = re.sub(r"(?i)</li\s*>", "\n", s)
    s = re.sub(r"(?i)</ul\s*>", "\n\n", s)
    s = re.sub(r"(?i)<ul\b[^>]*>", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s

def build_pdf_file(text_in: str, provider_name: str, patient_token: str, recipient_type: str) -> Tuple[Optional[str], str, str]:
    if SimpleDocTemplate is None:
        return None, "", "PDF generator not available"

    text_in = (text_in or "").strip()
    provider_name = (provider_name or "").strip() or "Provider"
    patient_token = (patient_token or "").strip()
    recipient_type = (recipient_type or "").strip()
    if not text_in:
        return None, "", "No content"

    clinic_short = (os.environ.get("CLINIC_SHORT") or "Integra").strip() or "Integra"

    def safe_token(s: str) -> str:
        s = "".join(ch for ch in (s or "") if ch.isalnum() or ch in (" ", "_"))
        s = "_".join(s.strip().split())
        return s or "Unknown"

    def doctor_token(name: str) -> str:
        low = (name or "").lower()
        if "henry" in low and "reis" in low:
            return "DrReis"
        parts = [p for p in safe_token(name).split("_") if p]
        if not parts:
            return "DrProvider"
        return "Dr" + parts[-1]

    doc_tok = doctor_token(provider_name)
    px_tok = patient_token or "PxUnknown"
    today = datetime.utcnow().strftime("%Y%m%d")
    kind = recipient_type.lower() or "report"
    kind = "referral" if "special" in kind or "physician" in kind else kind
    kind = safe_token(kind)

    filename = f"{safe_token(clinic_short)}_{doc_tok}_{safe_token(px_tok)}_{today}_{kind}.pdf"
    out_path = os.path.join(tempfile.gettempdir(), f"ai4health_{uuid.uuid4().hex}.pdf")

    styles = getSampleStyleSheet()

    # Slightly smaller body and tighter leading to fit more on the page while preserving readability.
    base = ParagraphStyle(
        "base",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14.2,
        spaceAfter=5,
        alignment=TA_JUSTIFY,
    )
    head = ParagraphStyle(
        "head",
        parent=base,
        fontName="Helvetica-Bold",
        spaceBefore=8,
        spaceAfter=5,
        alignment=TA_LEFT,
    )
    mono = ParagraphStyle(
        "mono",
        parent=base,
        fontName="Helvetica",
        fontSize=10.5,
        leading=13.4,
        alignment=TA_LEFT,
        spaceAfter=0,
    )
    demo = ParagraphStyle(
        "demo",
        parent=mono,
        spaceAfter=1,
    )

    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def parse_compact_letter(text_raw: str) -> Dict[str, Any]:
        lines = (text_raw or "").splitlines()
        header: List[str] = []
        body: List[str] = []
        demo_data: Dict[str, str] = {
            "patient": "",
            "dob": "",
            "sex": "",
            "phn": "",
            "phone": "",
            "email": "",
            "address": "",
        }
        reason_value = ""

        in_demo = False
        after_reason = False
        for raw in lines:
            line = (raw or "").rstrip()
            lower = line.strip().lower()
            if lower.startswith("reason for referral"):
                after_reason = True
                in_demo = False
                reason_value = line.split(":", 1)[1].strip() if ":" in line else ""
                continue

            if lower.startswith("patient:") or lower.startswith("dob:") or lower.startswith("sex:") or lower.startswith("phn:") or lower.startswith("phone:") or lower.startswith("email:") or lower.startswith("address:"):
                in_demo = True
                try:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip()
                    if k == "patient":
                        demo_data["patient"] = v
                    elif k == "dob":
                        demo_data["dob"] = v
                    elif k == "sex":
                        demo_data["sex"] = v
                    elif k == "phn":
                        demo_data["phn"] = v
                    elif k == "phone":
                        demo_data["phone"] = v
                    elif k == "email":
                        demo_data["email"] = v
                    elif k == "address":
                        demo_data["address"] = v
                except Exception:
                    pass
                continue

            if lower.startswith("to:") or lower.startswith("from:") or lower.startswith("date:"):
                header.append(line)
                continue

            if not after_reason:
                # Skip blank spacer lines between header and demographics.
                if in_demo and (not line.strip()):
                    continue
                # Skip any other stray pre body line.
                if (not line.strip()) and (not header):
                    continue
                # If we have header lines but no reason yet, we ignore spacer lines.
                if (not line.strip()) and header:
                    continue
                # Anything else before reason is ignored to keep the layout deterministic.
                continue

            body.append(line)

        return {
            "header": header,
            "demo": demo_data,
            "reason": reason_value,
            "body": body,
        }

    story = []

    lh_path = os.path.join(app.static_folder, "letterhead.png")
    if os.path.exists(lh_path):
        try:
            img = RLImage(lh_path)
            img.drawHeight = 50
            img.drawWidth = 500
            story.append(img)
            story.append(Spacer(1, 8))
        except Exception:
            pass

    parsed = parse_compact_letter(text_in)

    for line in parsed.get("header") or []:
        if ":" in line:
            k, v = line.split(":", 1)
            story.append(Paragraph(f"<b>{esc(k.strip())}:</b> {esc(v.strip())}", mono))

    d = parsed.get("demo") or {}
    demo_1_parts = []
    if d.get("patient"):
        demo_1_parts.append(f"<b>Patient</b>: {esc(d.get('patient'))}")
    if d.get("dob"):
        demo_1_parts.append(f"<b>DOB</b>: {esc(d.get('dob'))}")
    if d.get("sex"):
        demo_1_parts.append(f"<b>Sex</b>: {esc(d.get('sex'))}")
    if d.get("phn"):
        demo_1_parts.append(f"<b>PHN</b>: {esc(d.get('phn'))}")
    demo_1 = "   |   ".join(demo_1_parts).strip()

    demo_2_parts = []
    if d.get("phone"):
        demo_2_parts.append(f"<b>Phone</b>: {esc(d.get('phone'))}")
    if d.get("email"):
        demo_2_parts.append(f"<b>Email</b>: {esc(d.get('email'))}")
    if d.get("address"):
        demo_2_parts.append(f"<b>Addr</b>: {esc(d.get('address'))}")
    demo_2 = "   |   ".join(demo_2_parts).strip()

    if demo_1:
        story.append(Spacer(1, 8))
        story.append(Paragraph(demo_1, demo))
        if demo_2:
            story.append(Paragraph(demo_2, demo))
        story.append(Spacer(1, 8))

    if parsed.get("reason"):
        story.append(Paragraph(f"<b>Reason for Referral:</b> {esc(parsed.get('reason'))}", base))
        story.append(Spacer(1, 10))

    for raw in parsed.get("body") or []:
        line = (raw or "").rstrip()
        if not line.strip():
            story.append(Spacer(1, 9))
            continue

        lower = line.strip().lower()

        if lower in {"clinical summary", "clinical summary:"}:
            continue

        if lower in {"exam findings", "exam findings:", "assessment", "assessment:", "plan", "plan:"}:
            title = line.strip().replace(":", "")
            story.append(Paragraph(f"<b>{esc(title)}</b>", head))
            continue

        if lower.startswith("dear "):
            story.append(Spacer(1, 6))
            story.append(Paragraph(esc(line), base))
            story.append(Spacer(1, 4))
            continue

        if lower.startswith("kind regards"):
            story.append(Spacer(1, 12))
            story.append(Paragraph("Kind regards,", base))
            sig_path = signature_image_for_provider(provider_name)
            if sig_path and os.path.exists(sig_path):
                try:
                    sig = RLImage(sig_path)
                    page_w = rl_letter[0]
                    max_width = int(page_w * 0.25)
                    max_height = 90
                    iw = float(sig.imageWidth)
                    ih = float(sig.imageHeight)
                    if iw > 0 and ih > 0:
                        scale = min(max_width / iw, max_height / ih)
                        sig.drawWidth = iw * scale
                        sig.drawHeight = ih * scale
                    story.append(Spacer(1, 6))
                    text_w = rl_letter[0] - 54 - 54
                    tbl = Table([[sig]], colWidths=[text_w])
                    tbl.setStyle(TableStyle([
                        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]))
                    story.append(tbl)
                except Exception:
                    story.append(Paragraph(esc(provider_name), base))
            else:
                story.append(Paragraph(esc(provider_name), base))
            continue

        story.append(Paragraph(esc(line), base))

    doc = SimpleDocTemplate(
        out_path,
        pagesize=rl_letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
        title=filename,
    )

    try:
        doc.build(story)
        return out_path, filename, ""
    except Exception as e:
        app.logger.exception("PDF build failed")
        return None, "", f"PDF build failed: {type(e).__name__}: {str(e)}"

def export_pdf_response(text_in: str, provider_name: str, patient_token: str, recipient_type: str):
    pdf_path, filename, err = build_pdf_file(text_in, provider_name, patient_token, recipient_type)
    if err or not pdf_path:
        code = 500 if "available" in (err or "").lower() else 400
        return jsonify({"error": err or "PDF failed"}), code
    return send_file(pdf_path, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.post("/export_pdf")
def export_pdf():
    payload = request.get_json(silent=True) or {}
    text_in = (payload.get("text") or "").strip()
    provider_name = (payload.get("provider_name") or "").strip() or "Provider"
    patient_token = (payload.get("patient_token") or "").strip()
    recipient_type = (payload.get("recipient_type") or "").strip()
    return export_pdf_response(text_in, provider_name, patient_token, recipient_type)


@app.post("/export_pdf_html")
def export_pdf_html():
    payload = request.get_json(silent=True) or {}
    html_in = (payload.get("html") or "").strip()
    provider_name = (payload.get("provider_name") or "").strip() or "Provider"
    patient_token = (payload.get("patient_token") or "").strip()
    recipient_type = (payload.get("recipient_type") or "").strip()
    text_in = html_to_text_for_pdf(html_in)
    return export_pdf_response(text_in, provider_name, patient_token, recipient_type)

def smtp_settings() -> Dict[str, str]:
    return {
        "host": (os.getenv("SMTP_HOST") or "").strip(),
        "port": (os.getenv("SMTP_PORT") or "").strip(),
        "user": (os.getenv("SMTP_USER") or "").strip(),
        "password": (os.getenv("SMTP_PASS") or "").strip(),
        "from": (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "").strip(),
    }

def smtp_ready() -> Tuple[bool, str]:
    s = smtp_settings()
    if not s.get("host"):
        return False, "SMTP host not configured"
    if not s.get("port"):
        return False, "SMTP port not configured"
    if not s.get("from"):
        return False, "SMTP from address not configured"
    return True, ""

def send_pdf_via_email(to_email: str, subject: str, body: str, pdf_path: str, filename: str) -> Tuple[bool, str]:
    ok, msg = smtp_ready()
    if not ok:
        return False, msg
    s = smtp_settings()
    try:
        port = int(s.get("port") or "0")
    except Exception:
        return False, "SMTP port is invalid"

    try:
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
    except Exception:
        return False, "Could not read generated PDF"

    m = EmailMessage()
    m["From"] = s.get("from")
    m["To"] = to_email
    m["Subject"] = subject
    m.set_content(body or "")
    m.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)

    try:
        server = smtplib.SMTP(s.get("host"), port, timeout=20)
        try:
            server.ehlo()
            if port == 587:
                server.starttls()
                server.ehlo()
            if s.get("user") and s.get("password"):
                server.login(s.get("user"), s.get("password"))
            server.send_message(m)
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return True, ""
    except Exception as e:
        return False, f"Email failed: {type(e).__name__}: {str(e)}"


@app.post("/send_pdf_email")
def send_pdf_email():
    payload = request.get_json(silent=True) or {}
    to_email = (payload.get("to_email") or "").strip()
    subject = (payload.get("subject") or "").strip() or "Clinical letter"
    message = (payload.get("message") or "").strip()
    html_in = (payload.get("html") or "").strip()
    provider_name = (payload.get("provider_name") or "").strip() or "Provider"
    patient_token = (payload.get("patient_token") or "").strip()
    recipient_type = (payload.get("recipient_type") or "").strip()

    if not to_email:
        return jsonify({"ok": False, "error": "Recipient email is required"}), 200
    if not html_in:
        return jsonify({"ok": False, "error": "No letter content"}), 200

    ok, msg = smtp_ready()
    if not ok:
        return jsonify({"ok": False, "error": msg}), 200

    text_in = html_to_text_for_pdf(html_in)

    pdf_path, filename, build_err = build_pdf_file(text_in, provider_name, patient_token, recipient_type)
    if build_err or not pdf_path:
        return jsonify({"ok": False, "error": build_err or "PDF build failed"}), 200

    ok_send, err_send = send_pdf_via_email(to_email, subject, message, pdf_path, filename)
    try:
        os.remove(pdf_path)
    except Exception:
        pass

    if not ok_send:
        return jsonify({"ok": False, "error": err_send}), 200
    return jsonify({"ok": True}), 200

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