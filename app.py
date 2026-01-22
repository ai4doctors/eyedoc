
import os
import tempfile
import uuid
import json
import re
import shutil
import threading
import time
import io
import base64
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import PyPDF2
import requests
from flask import Flask, jsonify, render_template, request, send_file

try:
    import boto3
except Exception:
    boto3 = None

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

# Ensure pytesseract can find the tesseract binary on common hosts.
if pytesseract is not None:
    try:
        if shutil.which("tesseract") is None:
            for cand in ("/usr/bin/tesseract", "/usr/local/bin/tesseract"):
                if os.path.exists(cand):
                    pytesseract.pytesseract.tesseract_cmd = cand
                    break
    except Exception:
        pass

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

# Job storage
#
# Render often runs multiple workers. An in memory dict means a job created by one
# worker may be polled from another, producing "Unknown job_id".
#
# Store jobs on disk under /tmp so all workers can read the same state, while
# keeping an in memory cache for speed.
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
JOB_DIR = os.getenv("JOB_DIR", "/tmp/maneiro_jobs")

# Optional shared job storage in S3.
#
# Render can run multiple instances, and /tmp is not shared across instances.
# When enabled, jobs are mirrored into S3 so any instance can read status.
#
# Important: many AWS bucket policies allow writes only under specific prefixes
# (commonly "uploads/"). Using an uploads based default greatly reduces the
# chance of silent S3 put failures that would otherwise cause "processing" to
# snap back to "waiting" when a different instance handles status polling.
JOB_S3_PREFIX = (os.getenv("JOB_S3_PREFIX", "uploads/maneiro_jobs/") or "uploads/maneiro_jobs/").strip()
if not JOB_S3_PREFIX.endswith("/"):
    JOB_S3_PREFIX += "/"

def _job_path(job_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "", job_id or "")
    return os.path.join(JOB_DIR, f"{safe}.json")

def _ensure_job_dir() -> None:
    try:
        os.makedirs(JOB_DIR, exist_ok=True)
    except Exception:
        pass

def aws_ready() -> Tuple[bool, str]:
    if boto3 is None:
        return False, "boto3 not installed"
    bucket = os.getenv("AWS_S3_BUCKET", "").strip()
    region = os.getenv("AWS_REGION", "").strip()
    if not bucket:
        return False, "AWS_S3_BUCKET not set"
    if not region:
        return False, "AWS_REGION not set"
    return True, ""

def aws_clients():
    region = os.getenv("AWS_REGION", "").strip() or None
    s3 = boto3.client("s3", region_name=region)
    transcribe = boto3.client("transcribe", region_name=region)
    return s3, transcribe

def job_s3_enabled() -> bool:
    if boto3 is None:
        return False
    bucket = os.getenv("AWS_S3_BUCKET", "").strip()
    region = os.getenv("AWS_REGION", "").strip()
    if not bucket or not region:
        return False
    return True

def job_s3_key(job_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "", job_id or "")
    return f"{JOB_S3_PREFIX}{safe}.json"

def job_s3_key_fallbacks(job_id: str) -> List[str]:
    """Return possible S3 keys for a job, including legacy prefixes."""
    safe = re.sub(r"[^a-zA-Z0-9_]", "", job_id or "")
    keys = [f"{JOB_S3_PREFIX}{safe}.json"]
    # Legacy default from earlier builds
    legacy = "maneiro_jobs/"
    if not legacy.endswith("/"):
        legacy += "/"
    keys.append(f"{legacy}{safe}.json")
    # Also try under uploads in case a stricter policy is in place
    keys.append(f"uploads/maneiro_jobs/{safe}.json")
    # Deduplicate while preserving order
    out: List[str] = []
    for k in keys:
        if k not in out:
            out.append(k)
    return out

def s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"

def start_transcribe_job(job_name: str, media_key: str, language: str, mode: str = "dictation") -> Tuple[bool, str]:
    ok, msg = aws_ready()
    if not ok:
        return False, msg
    bucket = os.getenv("AWS_S3_BUCKET", "").strip()
    s3, transcribe = aws_clients()
    media_uri = s3_uri(bucket, media_key)
    args: Dict[str, Any] = {
        "TranscriptionJobName": job_name,
        "Media": {"MediaFileUri": media_uri},
        "OutputBucketName": bucket,
    }
    if language and language != "auto":
        args["LanguageCode"] = language
    else:
        args["IdentifyLanguage"] = True
        args["LanguageOptions"] = ["en-US", "pt-BR", "es-US", "fr-CA"]

    # Live exam mode enables speaker labels so downstream analysis can distinguish actors.
    if (mode or "").strip().lower() == "live":
        args["Settings"] = {
            "ShowSpeakerLabels": True,
            "MaxSpeakerLabels": 4,
        }
    try:
        transcribe.start_transcription_job(**args)
    except Exception as e:
        return False, str(e)
    return True, ""

def fetch_transcribe_result(job_name: str) -> Tuple[str, str, str]:
    ok, msg = aws_ready()
    if not ok:
        return "", "", msg
    bucket = os.getenv("AWS_S3_BUCKET", "").strip()
    s3, transcribe = aws_clients()

    # Fast path: if the output JSON is already in S3, read it directly.
    # This avoids a common failure mode where GetTranscriptionJob fails
    # (permissions or wrong region) which otherwise leaves the UI stuck.
    try:
        guess_key = f"{job_name}.json"
        obj = s3.get_object(Bucket=bucket, Key=guess_key)
        body = obj["Body"].read()
        data = json.loads(body.decode("utf-8", errors="ignore"))
        txt = transcribe_json_to_text(data)
        if txt:
            return txt, "completed", ""
    except Exception:
        pass
    try:
        r = transcribe.get_transcription_job(TranscriptionJobName=job_name)
    except Exception as e:
        # Make this a real failure so we do not loop forever on errors.
        return "", "failed", str(e)
    job = (r or {}).get("TranscriptionJob") or {}
    status = (job.get("TranscriptionJobStatus") or "").lower()
    if status not in ("completed", "failed"):
        return "", status, ""
    if status == "failed":
        return "", status, str(job.get("FailureReason") or "Transcription failed")

    out_uri = (((job.get("Transcript") or {}).get("TranscriptFileUri")) or "").strip()
    if not out_uri:
        return "", status, "Missing transcript uri"

    # If output is in the bucket, pull it from S3 for private buckets
    key = ""
    if ".amazonaws.com/" in out_uri:
        key = out_uri.split(".amazonaws.com/", 1)[1]
        key = key.split("?", 1)[0]
    if not key:
        return "", status, "Unable to parse transcript key"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        data = json.loads(body.decode("utf-8", errors="ignore"))
        txt = transcribe_json_to_text(data)
        return txt, status, ""
    except Exception as e:
        return "", status, str(e)

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def clamp_text(s: str, limit: int) -> str:
    return (s or "")[:limit]


def transcribe_json_to_text(data: Dict[str, Any]) -> str:
    try:
        results = data.get("results") or {}
        speaker = results.get("speaker_labels") or {}
        segments = speaker.get("segments") or []
        items = results.get("items") or []
        if segments and items:
            # Map time to words, then rebuild per segment.
            by_time = {}
            for it in items:
                st = it.get("start_time")
                alts = it.get("alternatives") or []
                content = (alts[0].get("content") if alts else "") or ""
                if st and content:
                    by_time.setdefault(st, []).append(content)

            lines = []
            for seg in segments:
                label = (seg.get("speaker_label") or "Speaker").replace("spk_", "Speaker ")
                seg_items = seg.get("items") or []
                words = []
                for sit in seg_items:
                    st = sit.get("start_time")
                    if st and st in by_time:
                        words.extend(by_time.get(st) or [])
                line = (" ".join(words)).strip()
                if line:
                    lines.append(f"{label}: {line}")
            if lines:
                return "\n".join(lines).strip()
    except Exception:
        pass
    try:
        txt = (((data.get("results") or {}).get("transcripts") or [{}])[0].get("transcript") or "").strip()
        return txt
    except Exception:
        return ""

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
    """Return OCR text and an error string.

    This uses the same rendering approach that worked in the older manual OCR flow
    (page.get_pixmap with a fixed dpi). We keep it bounded by max_pages.
    """
    if fitz is None or Image is None or pytesseract is None:
        return "", "OCR dependencies missing"
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return "", f"Could not open PDF for OCR: {e}"

    def prep(img):
        """Lightweight preprocessing to improve OCR on scanned pages."""
        try:
            g = img.convert("L")
        except Exception:
            g = img
        try:
            # Simple contrast stretch
            g = Image.eval(g, lambda x: 0 if x < 15 else (255 if x > 240 else x))
        except Exception:
            pass
        return g

    parts: List[str] = []
    try:
        pages = min(len(doc), max_pages)
        for i in range(pages):
            try:
                page = doc.load_page(i)
                pix = page.get_pixmap(dpi=220, alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                img = prep(img)
                txt = pytesseract.image_to_string(img, config="--psm 6") or ""
                parts.append(txt)
            except Exception:
                parts.append("")

        joined = "\n".join(parts).strip()
        if (not joined) or (len(joined) < 200):
            # Retry first pages at higher dpi
            retry_pages = min(pages, 3)
            retry: List[str] = []
            for i in range(retry_pages):
                try:
                    page = doc.load_page(i)
                    pix = page.get_pixmap(dpi=300, alpha=False)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    img = prep(img)
                    txt = pytesseract.image_to_string(img, config="--psm 6") or ""
                    retry.append(txt)
                except Exception:
                    retry.append("")
            parts = retry + parts[retry_pages:]
    except Exception as e:
        return "", f"OCR failed: {e}"
    finally:
        try:
            doc.close()
        except Exception:
            pass
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
    try:
        _ = pytesseract.get_tesseract_version()
    except Exception as e:
        return False, f"tesseract not available: {e}"
    return True, ""

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
    ocr_text, ocr_err = ocr_pdf_bytes(pdf_bytes)
    if ocr_err:
        return "", False, False, ocr_err
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
1a provider_name must be the authoring clinician only. Never include the patient name inside provider_name.
2 patient_block must contain patient demographics only. Include PHN if present. Exclude provider address and clinic address. Use <br> line breaks.
3 summary_html should be a clean summary section with headings and paragraphs. Use <b> for headings and <p> blocks. No markdown.
4 diagnoses must be problem list style, include laterality and severity when present.
5 plan bullets must be actionable, conservative, and aligned to diagnoses.
6 If exam findings are present, include them in summary_html with clear headings such as Exam findings and Imaging when applicable.


Encounter note:
{excerpt}
""".strip()

def extract_patient_name_from_block(pb_html: str) -> str:
    if not pb_html:
        return ""
    txt = re.sub(r"<\s*br\s*/?\s*>", "\n", pb_html, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = txt.strip()
    if not txt:
        return ""
    first = txt.split("\n", 1)[0].strip()
    if ":" in first:
        k, v = first.split(":", 1)
        if k.strip().lower() in {"patient", "name", "patient name"}:
            return v.strip()
    return first

def normalize_provider_name(provider_name: str, patient_name: str) -> str:
    pn = (provider_name or "").strip()
    pat = (patient_name or "").strip()
    if not pn:
        return ""
    if not pat:
        return pn
    low_pn = pn.lower()
    low_pat = pat.lower()
    if low_pat in low_pn:
        cleaned = re.sub(re.escape(pat), " ", pn, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned
    return pn

def pubmed_fetch_for_terms(terms: List[str], max_items: int = 12) -> List[Dict[str, str]]:
    # NCBI E utilities. Keep it light to avoid rate limits.
    uniq_terms: List[str] = []
    for t in (terms or []):
        t = (t or "").strip()
        if t and t.lower() not in [x.lower() for x in uniq_terms]:
            uniq_terms.append(t)

    blob = " ".join(uniq_terms).lower()

    def add_queries_for_subspecialty(b: str) -> List[str]:
        q: List[str] = []
        if any(k in b for k in ["dry eye", "meibomian", "mgd", "blepharitis", "ocular surface", "rosacea"]):
            q += [
                "TFOS DEWS" ,
                "dry eye disease guideline ophthalmology",
            ]
        if any(k in b for k in ["cornea", "keratitis", "corneal", "ulcer", "ectasia", "keratoconus"]):
            q += [
                "infectious keratitis clinical guideline ophthalmology",
                "keratoconus global consensus",
            ]
        if "cataract" in b:
            q += ["cataract preferred practice pattern ophthalmology", "cataract guideline ophthalmology"]
        if any(k in b for k in ["glaucoma", "ocular hypertension", "iop"]):
            q += ["glaucoma preferred practice pattern", "European Glaucoma Society guidelines"]
        if any(k in b for k in ["strabismus", "amblyopia", "esotropia", "exotropia"]):
            q += ["amblyopia preferred practice pattern", "strabismus clinical practice guideline"]
        if any(k in b for k in ["pediatric", "paediatric", "child", "infant"]):
            q += ["pediatric eye evaluations preferred practice pattern", "retinopathy of prematurity guideline"]
        if any(k in b for k in ["optic neuritis", "papilledema", "neuro", "visual field defect", "sixth nerve", "third nerve", "fourth nerve"]):
            q += ["optic neuritis guideline", "papilledema evaluation guideline"]
        if any(k in b for k in ["retina", "macular", "amd", "diabetic retinopathy", "retinal detachment", "uveitis", "vitreous"]):
            q += [
                "diabetic retinopathy preferred practice pattern",
                "age related macular degeneration preferred practice pattern",
                "retinal detachment guideline",
            ]
        return q

    canonical_queries = add_queries_for_subspecialty(blob)

    case_queries: List[str] = []
    for term in uniq_terms[:8]:
        case_queries.append(f"({term}) ophthalmology")
        case_queries.append(f"({term}) (guideline OR consensus OR \"preferred practice pattern\" OR systematic review OR meta analysis)")

    if not canonical_queries and not case_queries:
        case_queries = ["ophthalmology clinical practice guideline"]

    queries = (canonical_queries[:6] + case_queries[:10])

    pmids: List[str] = []
    for q in queries:
        try:
            r = requests.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pubmed", "term": q, "retmax": 12, "retmode": "json"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            ids = (data.get("esearchresult") or {}).get("idlist") or []
            for pid in ids:
                if pid not in pmids:
                    pmids.append(pid)
            if len(pmids) >= 40:
                break
        except Exception:
            continue

    if not pmids:
        return []

    pmids = pmids[:40]

    oph_journals = {
        "ophthalmology",
        "american journal of ophthalmology",
        "jama ophthalmology",
        "british journal of ophthalmology",
        "the ocular surface",
        "cornea",
        "investigative ophthalmology & visual science",
        "iovs",
        "retina",
        "graefe's archive for clinical and experimental ophthalmology",
        "clinical ophthalmology",
        "survey of ophthalmology",
        "eye",
        "acta ophthalmologica",
    }

    def parse_year(pubdate: str) -> int:
        m = re.search(r"(19\d{2}|20\d{2})", pubdate or "")
        return int(m.group(1)) if m else 0

    def score_item(title: str, source: str, pubdate: str) -> float:
        t = (title or "").lower()
        s = (source or "").lower()
        y = parse_year(pubdate)
        score = 0.0
        if any(k in t for k in ["preferred practice pattern", "guideline", "consensus", "position statement", "recommendation"]):
            score += 10.0
        if any(k in t for k in ["systematic review", "meta analysis", "meta-analysis"]):
            score += 7.0
        if any(k in t for k in ["review"]):
            score += 3.0
        if any(j in s for j in oph_journals):
            score += 4.0
        if any(k in t for k in ["tfos", "dews", "european glaucoma society", "egs"]):
            score += 6.0
        if y:
            score += max(0.0, min(5.0, (y - 2010) / 3.0))
        return score

    try:
        r = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("result") or {}

        scored: List[Tuple[float, str, Dict[str, Any]]] = []
        for pid in pmids:
            item = result.get(pid) or {}
            title = (item.get("title") or "").strip().rstrip(".")
            source = (item.get("source") or "").strip()
            pubdate = (item.get("pubdate") or "").strip()
            scored.append((score_item(title, source, pubdate), pid, item))

        scored.sort(key=lambda x: x[0], reverse=True)

        out: List[Dict[str, str]] = []
        for i, (_, pid, item) in enumerate(scored[:max_items], start=1):
            title = (item.get("title") or "").strip().rstrip(".")
            source = (item.get("source") or "").strip()
            pubdate = (item.get("pubdate") or "").strip()
            authors = item.get("authors") or []
            first_author = (authors[0].get("name") if authors else "") or ""
            citation = " ".join([x for x in [first_author, title, source, pubdate] if x]).strip()
            out.append({
                "number": str(i),
                "pmid": pid,
                "citation": citation,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                "source": "PubMed",
            })
        return out
    except Exception:
        return []



def canonical_reference_pool(labels):
    blob = " ".join([str(x or "") for x in (labels or [])]).lower()
    pool = []

    def add(pmid, citation, url="", source=""):
        pool.append({
            "pmid": (pmid or ""),
            "citation": (citation or ""),
            "url": (url or ""),
            "source": (source or ""),
        })

    if any(k in blob for k in ["dry eye", "meibomian", "mgd", "blepharitis", "ocular surface", "rosacea"]):
        add("41005521", "TFOS DEWS III: Executive Summary. Am J Ophthalmol. 2025.", "https://pubmed.ncbi.nlm.nih.gov/41005521/", "PubMed")
        add("", "TFOS DEWS III reports hub. Tear Film and Ocular Surface Society.", "https://www.tearfilm.org/paginades-tfos_dews_iii/7399_7239/eng/", "TFOS")
        add("28797892", "TFOS DEWS II Report Executive Summary. Ocul Surf. 2017.", "https://pubmed.ncbi.nlm.nih.gov/28797892/", "PubMed")
        add("", "TFOS DEWS II Executive Summary PDF. TearFilm.org.", "https://www.tearfilm.org/public/TFOSDEWSII-Executive.pdf", "TFOS")

    if "myopia" in blob:
        add("", "International Myopia Institute. IMI White Papers. Invest Ophthalmol Vis Sci. 2019.", "https://iovs.arvojournals.org/article.aspx?articleid=2738327", "ARVO")

    if any(k in blob for k in ["glaucoma", "intraocular pressure", "iop", "ocular hypertension"]):
        add("34933745", "Primary Open Angle Glaucoma Preferred Practice Pattern. Ophthalmology. 2021.", "https://pubmed.ncbi.nlm.nih.gov/34933745/", "PubMed")
        add("", "AAO PPP: Primary Open Angle Glaucoma. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern/primary-open-angle-glaucoma-ppp", "AAO")
        add("34675001", "European Glaucoma Society Terminology and Guidelines for Glaucoma, 5th Edition. Br J Ophthalmol. 2021.", "https://pubmed.ncbi.nlm.nih.gov/34675001/", "PubMed")
        add("", "EGS Guidelines download page. European Glaucoma Society.", "https://eugs.org/educational_materials/6", "EGS")

    if any(k in blob for k in ["diabetic retinopathy", "diabetes", "retinopathy"]):
        add("", "Standards of Care in Diabetes. American Diabetes Association.", "https://diabetesjournals.org/care/issue", "ADA")
        add("", "AAO PPP: Diabetic Retinopathy. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern/diabetic-retinopathy-ppp", "AAO")

    if any(k in blob for k in ["macular degeneration", "age related macular", "amd"]):
        add("39918524", "Age Related Macular Degeneration Preferred Practice Pattern. Ophthalmology. 2025.", "https://pubmed.ncbi.nlm.nih.gov/39918524/", "PubMed")
        add("", "AAO PPP: Age Related Macular Degeneration. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern/age-related-macular-degeneration-ppp", "AAO")
        add("18550876", "Age related macular degeneration. N Engl J Med. 2008.", "https://pubmed.ncbi.nlm.nih.gov/18550876/", "PubMed")

    if any(k in blob for k in ["keratoconus", "ectasia", "corneal ectasia"]):
        add("", "Global Consensus on Keratoconus and Ectatic Diseases. 2015.", "https://pubmed.ncbi.nlm.nih.gov/26253489/", "PubMed")

    if any(k in blob for k in ["cornea", "keratitis", "corneal", "ulcer"]):
        add("26253489", "Global Consensus on Keratoconus and Ectatic Diseases. Cornea. 2015.", "https://pubmed.ncbi.nlm.nih.gov/26253489/", "PubMed")
        add("", "AAO PPP: Bacterial Keratitis. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern/bacterial-keratitis-ppp", "AAO")
        add("", "AAO PPP: Corneal Ectasia. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern", "AAO")

    if "uveitis" in blob:
        add("", "Standardization of Uveitis Nomenclature. Key consensus publications.", "https://pubmed.ncbi.nlm.nih.gov/16490958/", "PubMed")

    if "cataract" in blob:
        add("34780842", "Cataract in the Adult Eye Preferred Practice Pattern. Ophthalmology. 2022.", "https://pubmed.ncbi.nlm.nih.gov/34780842/", "PubMed")
        add("", "AAO PPP PDF: Cataract in the Adult Eye. American Academy of Ophthalmology.", "https://www.aao.org/Assets/1d1ddbad-c41c-43fc-b5d3-3724fadc5434/637723154868200000/cataract-in-the-adult-eye-ppp-pdf", "AAO")

    if any(k in blob for k in ["strabismus", "amblyopia", "esotropia", "exotropia"]):
        add("", "AAO PPP: Amblyopia. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern/amblyopia-ppp", "AAO")
        add("", "AAO PPP: Esotropia and Exotropia. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern/esotropia-exotropia-ppp", "AAO")

    if any(k in blob for k in ["pediatric", "paediatric", "child", "infant"]):
        add("", "AAO PPP: Pediatric Eye Evaluations. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern/pediatric-eye-evaluations-ppp", "AAO")
        add("", "AAO PPP: Retinopathy of Prematurity. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern/retinopathy-of-prematurity-ppp", "AAO")

    if any(k in blob for k in ["optic neuritis", "papilledema", "neuro", "visual field", "third nerve", "fourth nerve", "sixth nerve"]):
        add("", "AAO EyeWiki: Optic Neuritis overview and evidence links. American Academy of Ophthalmology.", "https://eyewiki.aao.org/Optic_Neuritis", "EyeWiki")
        add("", "AAO EyeWiki: Papilledema overview and workup. American Academy of Ophthalmology.", "https://eyewiki.aao.org/Papilledema", "EyeWiki")

    if any(k in blob for k in ["retina", "macular", "amd", "diabetic retinopathy", "retinal detachment"]):
        add("", "AAO PPP: Retina and Vitreous. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern", "AAO")

    if any(k in blob for k in ["retinal detachment", "rhegmatogenous", "rd"]):
        add("", "AAO PPP: Posterior Segment and Retina guidelines hub. American Academy of Ophthalmology.", "https://www.aao.org/education/preferred-practice-pattern", "AAO")

    return pool[:10]


def merge_references(pubmed_refs, canonical_refs, max_total=18):
    seen = set()
    merged = []

    def norm_cit(s):
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    def key_for(r):
        pmid = (r.get("pmid") or "").strip()
        if pmid:
            return "pmid:" + pmid
        return "cit:" + norm_cit(r.get("citation"))

    for r in (pubmed_refs or []):
        if not isinstance(r, dict):
            continue
        k = key_for(r)
        if k in seen:
            continue
        seen.add(k)
        merged.append({
            "pmid": (r.get("pmid") or ""),
            "citation": (r.get("citation") or ""),
            "url": (r.get("url") or ""),
            "source": (r.get("source") or ""),
        })

    for r in (canonical_refs or []):
        if not isinstance(r, dict):
            continue
        k = key_for(r)
        if k in seen:
            continue
        seen.add(k)
        merged.append({
            "pmid": (r.get("pmid") or ""),
            "citation": (r.get("citation") or ""),
            "url": (r.get("url") or ""),
            "source": (r.get("source") or ""),
        })

    numbered = []
    for i, r in enumerate(merged[:max_total], start=1):
        pmid = (r.get("pmid") or "").strip()
        url = (r.get("url") or "").strip()
        if (not url) and pmid:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        numbered.append({
            "number": str(i),
            "pmid": pmid,
            "citation": (r.get("citation") or ""),
            "url": url,
            "source": (r.get("source") or ("PubMed" if pmid else "")),
        })
    return numbered


def preferred_ref_numbers(label, references):
    l = (label or "").lower()
    prefs = []

    def add_if(match):
        for ref in (references or []):
            n = ref.get("number")
            if not str(n).isdigit():
                continue
            num = int(n)
            pmid = (ref.get("pmid") or "").strip()
            cit = (ref.get("citation") or "").lower()
            if match(pmid, cit):
                prefs.append(num)

    if any(k in l for k in ["dry eye", "meibomian", "mgd", "blepharitis", "ocular surface"]):
        add_if(lambda pmid, cit: ("dews" in cit) or (pmid in {"41005521", "28736327"}))

    if "myopia" in l:
        add_if(lambda pmid, cit: ("myopia institute" in cit) or ("imi" in cit and "myopia" in cit))

    if any(k in l for k in ["glaucoma", "ocular hypertension", "iop"]):
        add_if(lambda pmid, cit: ("glaucoma" in cit) or ("preferred practice pattern" in cit))

    if any(k in l for k in ["diabetic", "retinopathy", "diabetes"]):
        add_if(lambda pmid, cit: ("diabetic" in cit) or ("standards of care" in cit))

    if any(k in l for k in ["macular degeneration", "amd"]):
        add_if(lambda pmid, cit: ("areds" in cit) or ("macular degeneration" in cit))

    if any(k in l for k in ["keratoconus", "ectasia"]):
        add_if(lambda pmid, cit: ("keratoconus" in cit) or ("ectatic" in cit))

    if "uveitis" in l:
        add_if(lambda pmid, cit: ("uveitis" in cit) or ("nomenclature" in cit))

    if "cataract" in l:
        add_if(lambda pmid, cit: "cataract" in cit)

    if any(k in l for k in ["retinal detachment", "rhegmatogenous", "rd"]):
        add_if(lambda pmid, cit: "retinal detachment" in cit)

    # De dup while keeping order
    out = []
    seen = set()
    for n in prefs:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def pad_refs(existing, preferred, all_nums, target=3):
    out = []
    for n in (existing or []):
        if isinstance(n, int) and n > 0 and n not in out:
            out.append(n)
    for n in (preferred or []):
        if isinstance(n, int) and n > 0 and n not in out:
            out.append(n)
    for n in (all_nums or []):
        if isinstance(n, int) and n > 0 and n not in out:
            out.append(n)
    return out[:target]


def enforce_minimum_citations(analysis, target=3):
    refs = analysis.get("references") or []
    all_nums = [int(r.get("number")) for r in refs if str(r.get("number", "")).isdigit()]

    for dx in (analysis.get("diagnoses") or []):
        if not isinstance(dx, dict):
            continue
        pref = preferred_ref_numbers(dx.get("label") or "", refs)
        dx["refs"] = pad_refs(dx.get("refs"), pref, all_nums, target)

    for pl in (analysis.get("plan") or []):
        if not isinstance(pl, dict):
            continue
        text = " ".join([pl.get("title") or "", " ".join(pl.get("bullets") or [])])
        pref = preferred_ref_numbers(text, refs)
        pl["refs"] = pad_refs(pl.get("refs"), pref, all_nums, max(1, min(target, 2)))


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
    reason_label = (form.get("reason_label") or "Reason for Report").strip() or "Reason for Report"
    out_lang = (form.get("output_language") or "").strip()
    out_lang_line = f"Write the letter in {out_lang}." if out_lang and out_lang.lower() not in {"auto", "match", "match input"} else ""
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

Language:
{out_lang_line}

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
Patient: <full name>
DOB: <date> (<age>)
Sex: <sex>
PHN: <phn>
Phone: <phone>
Email: <email>
Address: <address or leave blank>
<blank line>
{reason_label}: <diagnosis chosen plus reason_detail if provided>
<blank line>
Then the salutation line.

For letter_html, render the same information using <p> blocks and preserve blank lines using spacing.

Body rules:
1 After the salutation, start with a referral narrative paragraph, not a section label. For physician letters, it should read like a real referral: Thank you for seeing <patient>, a <age> year old patient who presented with <chief complaint> and is being referred for <reason plus requested service>.
2 Add a second sentence that gives brief context and urgency if relevant.
3 Then use headings for Exam findings, Assessment, and Plan. Do not write a Clinical summary heading.
4 Include exam findings with granularity when available in the note. Prefer objective measurements, key negatives, imaging summaries, and relevant test results.
5 Do not include Evidence or Disclaimer sections in the letter. Do not include citations or bracket numbers in the letter body.
6 End with a closing paragraph that includes: appreciation for seeing the patient, a subtle comanagement collaboration signal, and a request for their impressions and recommendations. Then finish with Kind regards only. Do not add the authoring doctor name.

Form:
{json.dumps(form, ensure_ascii=False)}

Analysis:
{json.dumps(analysis, ensure_ascii=False)}
""".strip()

def finalize_signoff(letter_plain: str, provider_name: str, has_signature: bool) -> str:
    txt = (letter_plain or "").rstrip()
    if not txt:
        return ""
    lines = txt.splitlines()
    # remove trailing provider name if present
    if lines:
        last = lines[-1].strip()
        prov = (provider_name or "").strip()
        if prov and last.lower() == prov.lower():
            lines = lines[:-1]
    txt = "\n".join(lines).rstrip()
    if has_signature:
        return txt
    prov = (provider_name or "").strip()
    if not prov:
        return txt
    if txt.lower().endswith("kind regards,"):
        return txt + "\n" + prov
    # if Kind regards appears near end, still append provider on a new line
    if re.search(r"\bkind regards\b", txt, flags=re.IGNORECASE):
        return txt + "\n" + prov
    return txt + "\n\nKind regards,\n" + prov

def new_job_id() -> str:
    return f"job_{int(time.time() * 1000)}_{os.urandom(4).hex()}"

def set_job(job_id: str, **updates: Any) -> None:
    _ensure_job_dir()
    with JOBS_LOCK:
        job = JOBS.get(job_id) or {}
        job.update(updates)
        JOBS[job_id] = job
        path = _job_path(job_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(job, f, ensure_ascii=False)
        except Exception:
            pass

    # Mirror to S3 for multi instance deployments.
    if job_s3_enabled():
        bucket = os.getenv("AWS_S3_BUCKET", "").strip()
        try:
            s3, _ = aws_clients()
        except Exception:
            s3 = None
        if s3 is not None:
            body = json.dumps(job, ensure_ascii=False).encode("utf-8")
            # Try a small set of keys to accommodate restrictive bucket policies.
            for key in job_s3_key_fallbacks(job_id):
                try:
                    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
                    break
                except Exception:
                    continue

def get_job(job_id: str) -> Dict[str, Any]:
    _ensure_job_dir()
    with JOBS_LOCK:
        if job_id in JOBS:
            return dict(JOBS.get(job_id) or {})
    path = _job_path(job_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            job = json.load(f) or {}
        if isinstance(job, dict):
            with JOBS_LOCK:
                JOBS[job_id] = job
            return dict(job)
    except Exception:
        pass

    # Fallback to S3 shared store.
    if job_s3_enabled():
        bucket = os.getenv("AWS_S3_BUCKET", "").strip()
        try:
            s3, _ = aws_clients()
        except Exception:
            s3 = None
        if s3 is not None:
            for key in job_s3_key_fallbacks(job_id):
                try:
                    obj = s3.get_object(Bucket=bucket, Key=key)
                    body = obj["Body"].read()
                    job = json.loads(body.decode("utf-8", errors="ignore")) or {}
                    if isinstance(job, dict):
                        with JOBS_LOCK:
                            JOBS[job_id] = job
                        try:
                            with open(path, "w", encoding="utf-8") as f:
                                json.dump(job, f, ensure_ascii=False)
                        except Exception:
                            pass
                        return dict(job)
                except Exception:
                    continue
    return {}

def run_analysis_job(job_id: str, note_text: str) -> None:
    set_job(job_id, status="processing", updated_at=now_utc_iso())
    obj, err = llm_json(analyze_prompt(note_text))
    if err or not obj:
        set_job(job_id, status="error", error=err or "Analysis failed", updated_at=now_utc_iso())
        return

    analysis = dict(ANALYZE_SCHEMA)
    analysis.update(obj)

    # Derive patient_name from patient_block for UI convenience and for provider name cleanup
    pb = (analysis.get("patient_block") or "")
    pb_plain = re.sub(r"<\s*br\s*/?\s*>", "\n", pb, flags=re.IGNORECASE)
    pb_plain = re.sub(r"<[^>]+>", "", pb_plain)
    pb_lines = [ln.strip() for ln in pb_plain.splitlines() if ln.strip()]
    patient_name = ""
    if pb_lines:
        first = pb_lines[0]
        if ":" in first:
            k, v = first.split(":", 1)
            if k.strip().lower() in {"patient", "name"}:
                patient_name = v.strip()
        if not patient_name:
            patient_name = first.strip()
    analysis["patient_name"] = patient_name

    # If provider_name accidentally contains the patient name, strip it out
    prov = (analysis.get("provider_name") or "").strip()
    if prov and patient_name:
        low_prov = prov.lower()
        low_px = patient_name.lower()
        if low_px in low_prov:
            prov2 = re.sub(re.escape(patient_name), "", prov, flags=re.IGNORECASE).strip()
            prov2 = re.sub(r"\s{2,}", " ", prov2).strip(" ,")
            analysis["provider_name"] = prov2

    # Fetch PubMed references based on diagnoses
    terms = []
    for dx in analysis.get("diagnoses") or []:
        if isinstance(dx, dict):
            label = (dx.get("label") or "").strip()
            if label:
                terms.append(label)
    references = pubmed_fetch_for_terms(terms)
    canonical = canonical_reference_pool([dx.get('label') for dx in (analysis.get('diagnoses') or []) if isinstance(dx, dict)])
    analysis['references'] = merge_references(references, canonical)

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


def run_analysis_upload_job(job_id: str, filename: str, data: bytes, force_ocr: bool = False) -> None:
    """Extract text (with OCR when needed) then run analysis."""
    set_job(job_id, status="processing", updated_at=now_utc_iso())

    name = (filename or "").lower()
    note_text = ""

    try:
        if name.endswith(".pdf"):
            try:
                note_text = extract_pdf_text(io.BytesIO(data))
            except Exception:
                note_text = ""

            if force_ocr or (not text_is_meaningful(note_text)):
                ocr_text, ocr_err = ocr_pdf_bytes(data)
                if ocr_err:
                    set_job(job_id, status="error", error=ocr_err, updated_at=now_utc_iso())
                    return
                if ocr_text:
                    note_text = ocr_text

        elif name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            if Image is None or pytesseract is None:
                set_job(job_id, status="error", error="Image OCR dependencies missing", updated_at=now_utc_iso())
                return
            try:
                img = Image.open(io.BytesIO(data))
                note_text = (pytesseract.image_to_string(img) or "").strip()
            except Exception as e:
                set_job(job_id, status="error", error=f"Image OCR failed: {e}", updated_at=now_utc_iso())
                return
        else:
            set_job(job_id, status="error", error="Unsupported file type for analysis. Use Record exam or upload a PDF or image.", updated_at=now_utc_iso())
            return
    except Exception as e:
        set_job(job_id, status="error", error=f"Extraction failed: {e}", updated_at=now_utc_iso())
        return

    if not note_text:
        set_job(job_id, status="error", error="No text extracted", updated_at=now_utc_iso())
        return

    # Run the main LLM analysis
    run_analysis_job(job_id, note_text)

@app.get("/")
def index():
    return render_template("index.html", version=APP_VERSION)

@app.post("/analyze_start")
def analyze_start():
    file = request.files.get("file") or request.files.get("pdf")
    if not file:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    filename = (getattr(file, "filename", "") or "").lower()
    data = file.read()

    job_id = new_job_id()
    set_job(job_id, status="waiting", updated_at=now_utc_iso())

    force_ocr = (request.form.get("handwritten") or "").strip() in {"1", "true", "yes", "on"}
    t = threading.Thread(target=run_analysis_upload_job, args=(job_id, filename, data, force_ocr), daemon=True)
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


@app.post("/analyze_text_start")
def analyze_text_start():
    payload = request.get_json(silent=True) or {}
    note_text = (payload.get("text") or "").strip()
    if not note_text:
        return jsonify({"ok": False, "error": "Missing text"}), 400
    job_id = new_job_id()
    set_job(job_id, status="waiting", updated_at=now_utc_iso())
    t = threading.Thread(target=run_analysis_job, args=(job_id, note_text), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id}), 200


@app.post("/transcribe_start")
def transcribe_start():
    audio = request.files.get("audio")
    if not audio:
        return jsonify({"ok": False, "error": "No audio uploaded"}), 400
    language = (request.form.get("language") or "auto").strip()
    mode = (request.form.get("mode") or "dictation").strip()
    ok, msg = aws_ready()
    if not ok:
        return jsonify({"ok": False, "error": msg}), 200

    bucket = os.getenv("AWS_S3_BUCKET", "").strip()
    ext = os.path.splitext((getattr(audio, "filename", "") or ""))[1].lower()
    if not ext:
        ext = ".webm"
    key = f"uploads/{uuid.uuid4().hex}{ext}"
    s3, _ = aws_clients()
    try:
        s3.upload_fileobj(audio.stream, bucket, key)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200

    job_name = new_job_id()
    started, err = start_transcribe_job(job_name, key, language, mode=mode)
    if not started:
        return jsonify({"ok": False, "error": err}), 200
    set_job(job_name, status="transcribing", updated_at=now_utc_iso(), media_key=key, language=language, mode=mode)
    return jsonify({"ok": True, "job_id": job_name}), 200


@app.get("/transcribe_status")
def transcribe_status():
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"ok": False, "error": "Missing job_id"}), 400
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Unknown job_id"}), 404

    if (job.get("status") or "") in ("complete", "error"):
        return jsonify({"ok": True, **job}), 200

    txt, status, err = fetch_transcribe_result(job_id)
    if err and status == "failed":
        set_job(job_id, status="error", error=err, updated_at=now_utc_iso())
        return jsonify({"ok": True, **get_job(job_id)}), 200
    if status == "completed" and txt:
        set_job(job_id, status="complete", transcript=txt, updated_at=now_utc_iso())
        return jsonify({"ok": True, **get_job(job_id)}), 200
    # still transcribing
    set_job(job_id, status="transcribing", updated_at=now_utc_iso())
    return jsonify({"ok": True, **get_job(job_id)}), 200

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
    doc_type = (form.get("document_type") or "").strip()
    form["reason_label"] = "Reason for Referral" if doc_type.lower() == "specialist" else "Reason for Report"
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

    provider_name = (form.get("from_doctor") or form.get("provider_name") or "").strip()
    sig_client = bool(form.get("signature_present"))
    sig_file = True if signature_image_for_provider(provider_name) else False
    has_signature = sig_client or sig_file
    letter_plain = finalize_signoff(letter_plain, provider_name, has_signature)

    # Normalize reason label in the top section when models drift
    want_label = (form.get("reason_label") or "Reason for Report").strip()
    if want_label:
        if want_label.lower() == "reason for report":
            letter_plain = re.sub(r"^Reason\s+for\s+Referral\s*:", "Reason for Report:", letter_plain, flags=re.IGNORECASE | re.MULTILINE)
        else:
            letter_plain = re.sub(r"^Reason\s+for\s+Report\s*:", "Reason for Referral:", letter_plain, flags=re.IGNORECASE | re.MULTILINE)

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

@app.post("/export_pdf")
def export_pdf():
    if SimpleDocTemplate is None:
        return jsonify({"error": "PDF generator not available"}), 500

    payload = request.get_json(silent=True) or {}
    text_in = (payload.get("text") or "").strip()
    provider_name = (payload.get("provider_name") or "").strip() or "Provider"
    patient_token = (payload.get("patient_token") or "").strip()
    recipient_type = (payload.get("recipient_type") or "").strip()
    letterhead_data_url = (payload.get("letterhead_data_url") or "").strip()
    signature_data_url = (payload.get("signature_data_url") or "").strip()
    if not text_in:
        return jsonify({"error": "No content"}), 400

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
        last = parts[-1]
        return "Dr" + last

    doc_tok = doctor_token(provider_name)
    px_tok = patient_token or "PxUnknown"
    today = datetime.utcnow().strftime("%Y%m%d")
    kind = recipient_type.lower() or "report"
    kind = "referral" if "special" in kind or "physician" in kind else kind
    kind = safe_token(kind)

    filename = f"{safe_token(clinic_short)}_{doc_tok}_{safe_token(px_tok)}_{today}_{kind}.pdf"

    out_path = os.path.join(tempfile.gettempdir(), f"maneiro_{uuid.uuid4().hex}.pdf")

    def data_url_to_tempfile(data_url: str, prefix: str) -> Optional[str]:
        if not data_url or not data_url.startswith("data:"):
            return None
        try:
            header, b64 = data_url.split(",", 1)
            mime = header.split(";", 1)[0].split(":", 1)[1].strip().lower()
            ext = ".png"
            if "jpeg" in mime or "jpg" in mime:
                ext = ".jpg"
            raw = base64.b64decode(b64)
            path = os.path.join(tempfile.gettempdir(), f"maneiro_{prefix}_{uuid.uuid4().hex}{ext}")
            with open(path, "wb") as f:
                f.write(raw)
            return path
        except Exception:
            return None

    lh_override = data_url_to_tempfile(letterhead_data_url, "letterhead")
    sig_override = data_url_to_tempfile(signature_data_url, "signature")
    sig_path_effective = sig_override or signature_image_for_provider(provider_name)
    if sig_path_effective and os.path.exists(sig_path_effective):
        text_in = finalize_signoff(text_in, provider_name, True)
    if sig_path_effective:
        text_in = finalize_signoff(text_in, provider_name, True)

    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        "base",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13.5,
        spaceAfter=4,
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
        fontSize=10,
        leading=12.8,
        alignment=TA_LEFT,
        spaceAfter=0,
    )

    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def meaningful(v: str) -> bool:
        if not v:
            return False
        lv = v.strip().lower()
        return lv not in {"na", "n/a", "none", "unknown", ""}

    def emit_demographics(demo: dict) -> list:
        parts = []
        if meaningful(demo.get("patient")):
            parts.append(f"<b>Patient:</b> {esc(demo.get('patient'))}")
        if meaningful(demo.get("dob")):
            parts.append(f"<b>DOB:</b> {esc(demo.get('dob'))}")
        if meaningful(demo.get("sex")):
            parts.append(f"<b>Sex:</b> {esc(demo.get('sex'))}")
        if meaningful(demo.get("phn")):
            parts.append(f"<b>PHN:</b> {esc(demo.get('phn'))}")
        lines = []
        if parts:
            lines.append(Paragraph("  ".join(parts), mono))

        parts2 = []
        if meaningful(demo.get("phone")):
            parts2.append(f"<b>Phone:</b> {esc(demo.get('phone'))}")
        if meaningful(demo.get("email")):
            parts2.append(f"<b>Email:</b> {esc(demo.get('email'))}")
        addr = demo.get("address") or ""
        if meaningful(addr) and len(addr) <= 80:
            parts2.append(f"<b>Address:</b> {esc(addr)}")
        if parts2:
            lines.append(Paragraph("  ".join(parts2), mono))
        return lines

    story = []

    lh_path = lh_override or os.path.join(app.static_folder, "letterhead.png")
    if lh_path and os.path.exists(lh_path):
        try:
            img = RLImage(lh_path)
            img.drawHeight = 50
            img.drawWidth = 500
            story.append(img)
            story.append(Spacer(1, 8))
        except Exception:
            pass

    raw_lines = text_in.splitlines()
    demo_keys = {"patient", "dob", "sex", "phn", "phone", "email", "address"}
    demo_data = {}
    demo_active = False
    demo_emitted = False

    for raw in raw_lines:
        line = (raw or "").rstrip()
        if not line.strip():
            if demo_active and not demo_emitted:
                story.extend(emit_demographics(demo_data))
                demo_emitted = True
            story.append(Spacer(1, 8))
            continue

        lower = line.strip().lower()
        key = lower.split(":", 1)[0].strip() if ":" in lower else ""

        if key in demo_keys:
            demo_active = True
            try:
                demo_data[key] = line.split(":", 1)[1].strip()
            except Exception:
                demo_data[key] = ""
            continue

        if demo_active and not demo_emitted:
            story.extend(emit_demographics(demo_data))
            demo_emitted = True

        if lower in {"clinical summary", "clinical summary:"}:
            continue
        if lower.startswith("reason for referral") or lower.startswith("reason for report"):
            label = "Reason for Referral" if lower.startswith("reason for referral") else "Reason for Report"
            value = line.split(":", 1)[1].strip() if ":" in line else ""
            story.append(Spacer(1, 10))
            story.append(Paragraph(f"<b>{label}:</b> {esc(value)}", base))
            story.append(Spacer(1, 10))
            continue

        if lower in {"exam findings", "exam findings:", "assessment", "assessment:", "plan", "plan:"}:
            title = line.strip().replace(":", "")
            story.append(Paragraph(f"<b>{esc(title)}</b>", head))
            continue

        if lower.startswith("to:") or lower.startswith("from:") or lower.startswith("date:"):
            try:
                k, v = line.split(":", 1)
                story.append(Paragraph(f"<b>{esc(k)}:</b> {esc(v.strip())}", mono))
            except Exception:
                story.append(Paragraph(esc(line), mono))
            continue

        if lower.startswith("dear "):
            story.append(Spacer(1, 8))
            story.append(Paragraph(esc(line), base))
            story.append(Spacer(1, 6))
            continue

        if lower.startswith("kind regards"):
            story.append(Spacer(1, 12))
            story.append(Paragraph("Kind regards,", base))
            sig_path = sig_path_effective
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

    if demo_active and not demo_emitted:
        story.extend(emit_demographics(demo_data))

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
        return send_file(out_path, as_attachment=True, download_name=filename, mimetype="application/pdf")
    except Exception as e:
        app.logger.exception("PDF export failed")
        return jsonify({"error": f"PDF export failed: {type(e).__name__}: {str(e)}"}), 500


@app.get("/healthz")

def healthz():
    ok, msg = client_ready()
    ocr_ok, ocr_msg = ocr_ready()
    tpath = ""
    try:
        tpath = shutil.which("tesseract") or ""
    except Exception:
        tpath = ""
    return jsonify({
        "ok": True,
        "app_version": APP_VERSION,
        "time_utc": now_utc_iso(),
        "openai_ready": ok,
        "openai_message": msg,
        "ocr_ready": ocr_ok,
        "ocr_message": ocr_msg,
        "tesseract_path": tpath,
        "model": model_name(),
    }), 200