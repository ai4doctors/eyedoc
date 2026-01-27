"""
API Blueprint - All working endpoints from original app.py
"""
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
from flask import Blueprint, jsonify, request, send_file, current_app
from flask_login import login_required, current_user

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

# Ensure pytesseract can find the tesseract binary
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
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_JUSTIFY
    from reportlab.lib import colors
except Exception:
    SimpleDocTemplate = None
    rl_letter = None

api_bp = Blueprint('api', __name__)

# Job storage
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
JOB_DIR = os.getenv("JOB_DIR") or os.getenv("job_dir") or "/tmp/maneiro_jobs"
UPLOAD_DIR = os.getenv("UPLOAD_DIR") or os.getenv("upload_dir") or os.path.join(JOB_DIR, "uploads")

JOB_S3_PREFIX = (os.getenv("JOB_S3_PREFIX", "uploads/maneiro_jobs/") or "uploads/maneiro_jobs/").strip()
if not JOB_S3_PREFIX.endswith("/"):
    JOB_S3_PREFIX += "/"


# ============ Helper Functions ============

def _job_path(job_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "", job_id or "")
    return os.path.join(JOB_DIR, f"{safe}.json")


def _ensure_job_dir() -> None:
    try:
        os.makedirs(JOB_DIR, exist_ok=True)
    except Exception:
        pass
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
    except Exception:
        pass


def _upload_path(job_id: str, filename: str) -> str:
    safe_id = re.sub(r"[^a-zA-Z0-9_]", "", job_id or "")
    ext = os.path.splitext((filename or "").strip())[1].lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg", ".webp"):
        ext = ".bin"
    return os.path.join(UPLOAD_DIR, f"{safe_id}{ext}")


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
    return bool(bucket and region)


def job_s3_key(job_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "", job_id or "")
    return f"{JOB_S3_PREFIX}{safe}.json"


def job_s3_key_fallbacks(job_id: str) -> List[str]:
    safe = re.sub(r"[^a-zA-Z0-9_]", "", job_id or "")
    keys = [f"{JOB_S3_PREFIX}{safe}.json"]
    legacy = "maneiro_jobs/"
    keys.append(f"{legacy}{safe}.json")
    keys.append(f"uploads/maneiro_jobs/{safe}.json")
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


def parse_utc_iso(s: str) -> Optional[datetime]:
    try:
        if not s:
            return None
        txt = str(s).strip()
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def clamp_text(s: str, limit: int) -> str:
    return (s or "")[:limit]


def transcribe_json_to_text(data: Dict[str, Any]) -> str:
    try:
        results = data.get("results") or {}
        speaker = results.get("speaker_labels") or {}
        segments = speaker.get("segments") or []
        items = results.get("items") or []
        if segments and items:
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
    if fitz is None or Image is None or pytesseract is None:
        return "", "OCR dependencies missing"

    try:
        _ = pytesseract.get_tesseract_version()
    except Exception as e:
        return "", f"OCR engine not available: {e}"
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return "", f"Could not open PDF for OCR: {e}"

    def prep(img):
        try:
            g = img.convert("L")
        except Exception:
            g = img
        try:
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
    return OpenAI(api_key=key, timeout=60)


def safe_json_loads(s: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not s:
        return None, "Empty model output"
    
    # Strip markdown code blocks if present
    text = s.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        lines = text.split("\n", 1)
        if len(lines) > 1:
            text = lines[1]
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3].strip()
        elif "```" in text:
            text = text.rsplit("```", 1)[0].strip()
    
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj, ""
    except Exception:
        pass
    
    # Fallback: extract first JSON object from text
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
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
                {"role": "system", "content": "You are a JSON API. Return ONLY valid JSON with no markdown formatting, no code fences, no explanations. Start your response with { and end with }."},
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


def pubmed_fetch_for_terms(terms: List[str], max_items: int = 12) -> List[Dict[str, str]]:
    uniq_terms: List[str] = []
    for t in (terms or []):
        t = (t or "").strip()
        if t and t.lower() not in [x.lower() for x in uniq_terms]:
            uniq_terms.append(t)

    blob = " ".join(uniq_terms).lower()

    def add_queries_for_subspecialty(b: str) -> List[str]:
        q: List[str] = []
        if any(k in b for k in ["dry eye", "meibomian", "mgd", "blepharitis", "ocular surface", "rosacea"]):
            q += ["TFOS DEWS", "dry eye disease guideline ophthalmology"]
        if any(k in b for k in ["cornea", "keratitis", "corneal", "ulcer", "ectasia", "keratoconus"]):
            q += ["infectious keratitis clinical guideline ophthalmology", "keratoconus global consensus"]
        if "cataract" in b:
            q += ["cataract preferred practice pattern ophthalmology", "cataract guideline ophthalmology"]
        if any(k in b for k in ["glaucoma", "ocular hypertension", "iop"]):
            q += ["glaucoma preferred practice pattern", "European Glaucoma Society guidelines"]
        if any(k in b for k in ["strabismus", "amblyopia", "esotropia", "exotropia"]):
            q += ["amblyopia preferred practice pattern", "strabismus clinical practice guideline"]
        if any(k in b for k in ["pediatric", "paediatric", "child", "infant"]):
            q += ["pediatric eye evaluations preferred practice pattern", "retinopathy of prematurity guideline"]
        if any(k in b for k in ["optic neuritis", "papilledema", "neuro", "visual field defect"]):
            q += ["optic neuritis guideline", "papilledema evaluation guideline"]
        if any(k in b for k in ["retina", "macular", "amd", "diabetic retinopathy", "retinal detachment", "uveitis"]):
            q += ["diabetic retinopathy preferred practice pattern", "age related macular degeneration preferred practice pattern"]
        return q

    canonical_queries = add_queries_for_subspecialty(blob)
    case_queries: List[str] = []
    for term in uniq_terms[:8]:
        case_queries.append(f"({term}) ophthalmology")
        case_queries.append(f"({term}) (guideline OR consensus OR systematic review)")

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
        for i, pid in enumerate(pmids[:max_items], start=1):
            item = result.get(pid) or {}
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


def assign_citations_prompt(analysis: Dict[str, Any]) -> str:
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

Analysis:
{json.dumps(analysis, ensure_ascii=False)}
""".strip()


def letter_prompt(form: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    reason_label = (form.get("reason_label") or "Reason for Report").strip() or "Reason for Report"
    out_lang = (form.get("output_language") or "").strip()
    out_lang_line = f"Write the letter in {out_lang}." if out_lang and out_lang.lower() not in {"auto", "match"} else ""
    return f"""
You are a clinician assistant. Create an Output Communication report.

Output VALID JSON only with this schema:
letter_plain: string
letter_html: string

Tone rules:
If recipient_type equals "Patient", write in patient friendly accessible language while staying professional.
Otherwise write in technical physician style that is precise and concise.

Language:
{out_lang_line}

Clinic context:
clinic_name: {os.getenv("CLINIC_NAME","")}
clinic_address: {os.getenv("CLINIC_ADDRESS","")}
clinic_phone: {os.getenv("CLINIC_PHONE","")}

Structure:
Create a professional referral or report letter.

Required top section for letter_plain:
To: <recipient>
From: <authoring provider>
Date: <current date>

Patient: <full name>
DOB: <date> (<age>)
Sex: <sex>
PHN: <phn>
Phone: <phone>
Email: <email>

{reason_label}: <diagnosis chosen plus reason_detail if provided>

Then the salutation line.

Body rules:
1 Start with a referral narrative paragraph.
2 Use headings for Exam findings, Assessment, and Plan.
3 Do not include Evidence or Disclaimer sections.
4 End with Kind regards only.

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

    if job_s3_enabled():
        bucket = os.getenv("AWS_S3_BUCKET", "").strip()
        try:
            s3, _ = aws_clients()
        except Exception:
            s3 = None
        if s3 is not None:
            body = json.dumps(job, ensure_ascii=False).encode("utf-8")
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
    set_job(job_id, status="processing", updated_at=now_utc_iso(), heartbeat_at=now_utc_iso())
    obj, err = llm_json(analyze_prompt(note_text))
    
    # Heartbeat after LLM call
    set_job(job_id, heartbeat_at=now_utc_iso())
    
    if err or not obj:
        set_job(job_id, status="error", error=err or "Analysis failed", updated_at=now_utc_iso())
        return

    analysis = dict(ANALYZE_SCHEMA)
    analysis.update(obj)

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

    prov = (analysis.get("provider_name") or "").strip()
    if prov and patient_name:
        low_prov = prov.lower()
        low_px = patient_name.lower()
        if low_px in low_prov:
            prov2 = re.sub(re.escape(patient_name), "", prov, flags=re.IGNORECASE).strip()
            prov2 = re.sub(r"\s{2,}", " ", prov2).strip(" ,")
            analysis["provider_name"] = prov2

    # Heartbeat before PubMed fetch
    set_job(job_id, heartbeat_at=now_utc_iso())
    
    terms = []
    for dx in analysis.get("diagnoses") or []:
        if isinstance(dx, dict):
            label = (dx.get("label") or "").strip()
            if label:
                terms.append(label)
    references = pubmed_fetch_for_terms(terms)
    canonical = canonical_reference_pool([dx.get('label') for dx in (analysis.get('diagnoses') or []) if isinstance(dx, dict)])
    analysis['references'] = merge_references(references, canonical)

    # Heartbeat before citation assignment
    set_job(job_id, heartbeat_at=now_utc_iso())

    if analysis.get('references'):
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

    set_job(job_id, status="complete", data=analysis, updated_at=now_utc_iso())


def run_analysis_upload_job(job_id: str, filename: str, data: bytes, force_ocr: bool = False) -> None:
    set_job(job_id, status="processing", updated_at=now_utc_iso())
    set_job(job_id, heartbeat_at=now_utc_iso())

    try:
        if (not data) and job_id:
            job = get_job(job_id)
            up = (job.get("upload_path") or "").strip()
            if up and os.path.exists(up):
                with open(up, "rb") as f:
                    data = f.read()
                filename = (job.get("upload_name") or filename or "")
                if isinstance(job.get("force_ocr"), bool):
                    force_ocr = bool(job.get("force_ocr"))
    except Exception:
        pass

    name = (filename or "").lower()
    note_text = ""
    ocr_attempted = False

    try:
        job = get_job(job_id)
        upload_path = (job.get("upload_path") or "").strip() if isinstance(job, dict) else ""
        if upload_path and not data:
            try:
                with open(upload_path, "rb") as f:
                    data = f.read()
            except Exception as e:
                set_job(job_id, status="error", error=f"Failed to read uploaded file: {e}", updated_at=now_utc_iso())
                return

        if name.endswith(".pdf"):
            try:
                note_text = extract_pdf_text(io.BytesIO(data))
            except Exception:
                note_text = ""

            if force_ocr or (not text_is_meaningful(note_text)):
                ocr_attempted = True
                ocr_text, ocr_err = ocr_pdf_bytes(data)
                if ocr_err:
                    set_job(job_id, status="error", error=ocr_err, updated_at=now_utc_iso())
                    return
                if ocr_text:
                    note_text = ocr_text

            set_job(job_id, heartbeat_at=now_utc_iso())

        elif name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            ocr_attempted = True
            if Image is None or pytesseract is None:
                set_job(job_id, status="error", error="Image OCR dependencies missing", updated_at=now_utc_iso())
                return
            try:
                img = Image.open(io.BytesIO(data))
                note_text = (pytesseract.image_to_string(img) or "").strip()
            except Exception as e:
                set_job(job_id, status="error", error=f"Image OCR failed: {e}", updated_at=now_utc_iso())
                return
            set_job(job_id, heartbeat_at=now_utc_iso())
        else:
            set_job(job_id, status="error", error="Unsupported file type", updated_at=now_utc_iso())
            return
    except Exception as e:
        set_job(job_id, status="error", error=f"Extraction failed: {e}", updated_at=now_utc_iso())
        return

    if not note_text:
        msg = "No text extracted"
        if ocr_attempted:
            msg = "OCR returned no readable text"
        set_job(job_id, status="error", error=msg, updated_at=now_utc_iso())
        return

    run_analysis_job(job_id, note_text)


# ============ API Routes ============

@api_bp.route("/analyze_start", methods=["POST"])
@login_required
def analyze_start():
    file = request.files.get("file") or request.files.get("pdf")
    if not file:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    filename = (getattr(file, "filename", "") or "").lower()
    data = file.read()

    job_id = new_job_id()
    force_ocr = (request.form.get("handwritten") or "").strip() in {"1", "true", "yes", "on"}
    _ensure_job_dir()
    upath = _upload_path(job_id, filename)
    try:
        with open(upath, "wb") as f:
            f.write(data)
    except Exception:
        upath = ""

    set_job(
        job_id,
        status="waiting",
        updated_at=now_utc_iso(),
        upload_path=upath,
        upload_name=filename,
        force_ocr=force_ocr,
    )
    t = threading.Thread(target=run_analysis_upload_job, args=(job_id, filename, data, force_ocr), daemon=True)
    t.start()

    return jsonify({"ok": True, "job_id": job_id}), 200


@api_bp.route("/analyze_status", methods=["GET"])
@login_required
def analyze_status():
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"ok": False, "error": "Missing job_id"}), 400
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Unknown job_id"}), 404

    try:
        status = (job.get("status") or "").strip().lower()
        if status == "processing":
            hb = parse_utc_iso(job.get("heartbeat_at") or job.get("updated_at") or "")
            now = datetime.now(timezone.utc)
            stale_seconds = (now - hb).total_seconds() if hb else 999999
            # Only restart if stale for 90+ seconds (LLM calls can take 60s)
            if stale_seconds > 90:
                up = (job.get("upload_path") or "").strip()
                if up and os.path.exists(up) and not job.get("resume_started"):
                    set_job(job_id, resume_started=True, updated_at=now_utc_iso(), heartbeat_at=now_utc_iso())
                    t = threading.Thread(target=run_analysis_upload_job, args=(job_id, job.get("upload_name") or "", b"", bool(job.get("force_ocr"))), daemon=True)
                    t.start()
                    job = get_job(job_id)
    except Exception:
        pass
    return jsonify({"ok": True, **job}), 200


@api_bp.route("/analyze_text_start", methods=["POST"])
@login_required
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


@api_bp.route("/transcribe_start", methods=["POST"])
@login_required
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


@api_bp.route("/transcribe_status", methods=["GET"])
@login_required
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
    set_job(job_id, status="transcribing", updated_at=now_utc_iso())
    return jsonify({"ok": True, **get_job(job_id)}), 200


@api_bp.route("/generate_report", methods=["POST"])
@login_required
def generate_report():
    payload = request.get_json(silent=True) or {}
    form = payload.get("form") or {}
    analysis = payload.get("analysis") or {}

    pb_html = (analysis.get("patient_block") or "")
    pb_plain = re.sub(r"<\s*br\s*/?\s*>", "\n", pb_html, flags=re.IGNORECASE)
    pb_plain = re.sub(r"<[^>]+>", "", pb_plain)
    pb_plain = re.sub(r"\n{3,}", "\n\n", pb_plain).strip()
    analysis["patient_block_plain"] = pb_plain

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
    if letter_plain:
        letter_plain = re.sub(r"<\s*br\s*/?\s*>", "\n", letter_plain, flags=re.IGNORECASE)
        letter_plain = re.sub(r"<\s*/?p\s*>", "\n", letter_plain, flags=re.IGNORECASE)
        letter_plain = re.sub(r"<[^>]+>", "", letter_plain)
        letter_plain = re.sub(r"\n{3,}", "\n\n", letter_plain).strip()
    if not letter_plain:
        return jsonify({"ok": False, "error": "Empty output"}), 200

    provider_name = (form.get("from_doctor") or form.get("provider_name") or "").strip()
    sig_client = bool(form.get("signature_present"))
    letter_plain = finalize_signoff(letter_plain, provider_name, sig_client)

    want_label = (form.get("reason_label") or "Reason for Report").strip()
    if want_label:
        if want_label.lower() == "reason for report":
            letter_plain = re.sub(r"^Reason\s+for\s+Referral\s*:", "Reason for Report:", letter_plain, flags=re.IGNORECASE | re.MULTILINE)
        else:
            letter_plain = re.sub(r"^Reason\s+for\s+Report\s*:", "Reason for Referral:", letter_plain, flags=re.IGNORECASE | re.MULTILINE)

    return jsonify({"ok": True, "letter_plain": letter_plain, "letter_html": letter_html}), 200


@api_bp.route("/export_pdf", methods=["POST"])
@login_required
def export_pdf():
    if SimpleDocTemplate is None:
        return jsonify({"error": "PDF generator not available"}), 500

    payload = request.get_json(silent=True) or {}
    text_in = (payload.get("text") or "").strip()
    provider_name = (payload.get("provider_name") or "").strip() or "Provider"
    patient_token = (payload.get("patient_token") or "").strip()
    recipient_type = (payload.get("recipient_type") or "").strip()
    
    if not text_in:
        return jsonify({"error": "No content"}), 400

    clinic_short = (os.environ.get("CLINIC_SHORT") or "Integra").strip() or "Integra"

    def safe_token(s: str) -> str:
        s = "".join(ch for ch in (s or "") if ch.isalnum() or ch in (" ", "_"))
        s = "_".join(s.strip().split())
        return s or "Unknown"

    def doctor_token(name: str) -> str:
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

    styles = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=styles["Normal"], fontName="Helvetica", fontSize=10, leading=13.5, spaceAfter=4, alignment=TA_JUSTIFY)
    head = ParagraphStyle("head", parent=base, fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=5, alignment=TA_LEFT)
    mono = ParagraphStyle("mono", parent=base, fontName="Helvetica", fontSize=10, leading=12.8, alignment=TA_LEFT, spaceAfter=0)

    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story = []
    raw_lines = text_in.splitlines()

    for raw in raw_lines:
        line = (raw or "").rstrip()
        if not line.strip():
            story.append(Spacer(1, 8))
            continue

        lower = line.strip().lower()

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
        return send_file(out_path, as_attachment=True, download_name=filename, mimetype="application/pdf")
    except Exception as e:
        return jsonify({"error": f"PDF export failed: {type(e).__name__}: {str(e)}"}), 500


@api_bp.route("/healthz", methods=["GET"])
def healthz():
    ok, msg = client_ready()
    ocr_ok, ocr_msg = ocr_ready()
    return jsonify({
        "ok": True,
        "app_version": os.getenv("APP_VERSION", "2026.5"),
        "time_utc": now_utc_iso(),
        "openai_ready": ok,
        "openai_message": msg,
        "ocr_ready": ocr_ok,
        "ocr_message": ocr_msg,
        "model": model_name(),
    }), 200


# ============ ASSISTANT MODE ENDPOINTS ============

def triage_fax_prompt(summary_html: str, patient_block: str, full_text: str = "") -> str:
    """Prompt for triaging incoming fax/communication"""
    context = summary_html or full_text or ""
    return f"""
You are a clinic assistant helping triage incoming faxes and communications.

Analyze the document and extract:
1. FROM: Who sent this communication (doctor name, clinic name, or organization)
2. REGARDING: Brief description of what this is about (patient name if mentioned, type of document)
3. FRONT DESK ACTIONS: Tasks the front desk should do (e.g., file document, update patient record, schedule appointment, send records)
4. DOCTOR ACTIONS: Tasks requiring doctor attention (e.g., review results, sign forms, call back, clinical decision needed)

Output VALID JSON only:
{{
  "from": "string - sender name and organization",
  "regarding": "string - brief description",
  "front_desk_tasks": ["array of clear, actionable task strings"],
  "doctor_tasks": ["array of clear, actionable task strings"]
}}

Rules:
1. Tasks should be single, clear sentences suitable for copying into an EHR task list
2. If the document is routine (e.g., normal lab results), note that no urgent action is needed
3. If patient information is present, include it in the "regarding" field
4. Keep tasks specific and actionable

Patient info:
{patient_block}

Document content:
{context[:8000]}
""".strip()


def patient_letter_prompt(analysis: dict) -> str:
    """Prompt for generating patient-friendly letter"""
    return f"""
You are a clinic assistant writing a letter to a patient about their recent visit.

Write a warm, professional letter in patient-friendly language:
- Avoid medical jargon or explain terms simply
- Be reassuring but accurate
- Include key findings and next steps
- End with contact information for questions

Output VALID JSON only:
{{
  "letter": "string - the complete letter text"
}}

Patient info:
{analysis.get('patient_block', '')}

Provider:
{analysis.get('provider_name', '')}

Clinical summary:
{analysis.get('summary_html', '')[:4000]}

Diagnoses:
{json.dumps(analysis.get('diagnoses', []), ensure_ascii=False)}

Plan:
{json.dumps(analysis.get('plan', []), ensure_ascii=False)}
""".strip()


def insurance_letter_prompt(analysis: dict) -> str:
    """Prompt for generating insurance/prior authorization letter"""
    return f"""
You are a clinic assistant writing a letter for insurance purposes (prior authorization, medical necessity, or appeal).

Write a formal, clinical letter that:
- Uses proper medical terminology and ICD codes if available
- Clearly states the medical necessity
- References clinical findings and examination results
- Includes specific treatment recommendations
- Is structured for insurance review

Output VALID JSON only:
{{
  "letter": "string - the complete letter text"
}}

Patient info:
{analysis.get('patient_block', '')}

Provider:
{analysis.get('provider_name', '')}

Clinical summary:
{analysis.get('summary_html', '')[:4000]}

Diagnoses:
{json.dumps(analysis.get('diagnoses', []), ensure_ascii=False)}

Plan:
{json.dumps(analysis.get('plan', []), ensure_ascii=False)}
""".strip()


@api_bp.route("/triage_fax", methods=["POST"])
@login_required
def triage_fax():
    """Triage an incoming fax/communication for front desk"""
    payload = request.get_json(silent=True) or {}
    analysis = payload.get("analysis") or {}
    
    summary_html = analysis.get("summary_html", "")
    patient_block = analysis.get("patient_block", "")
    
    prompt = triage_fax_prompt(summary_html, patient_block)
    obj, err = llm_json(prompt, temperature=0.2)
    
    if err or not obj:
        return jsonify({"ok": False, "error": err or "Triage failed"}), 200
    
    return jsonify({
        "ok": True,
        "from": obj.get("from", ""),
        "regarding": obj.get("regarding", ""),
        "front_desk_tasks": obj.get("front_desk_tasks", []),
        "doctor_tasks": obj.get("doctor_tasks", [])
    }), 200


@api_bp.route("/generate_assistant_letter", methods=["POST"])
@login_required
def generate_assistant_letter():
    """Generate patient or insurance letter from analysis"""
    payload = request.get_json(silent=True) or {}
    analysis = payload.get("analysis") or {}
    letter_type = payload.get("letter_type", "patient")
    
    if letter_type == "insurance":
        prompt = insurance_letter_prompt(analysis)
    else:
        prompt = patient_letter_prompt(analysis)
    
    obj, err = llm_json(prompt, temperature=0.3)
    
    if err or not obj:
        return jsonify({"ok": False, "error": err or "Letter generation failed"}), 200
    
    letter = obj.get("letter", "")
    if not letter:
        return jsonify({"ok": False, "error": "Empty letter generated"}), 200
    
    return jsonify({"ok": True, "letter": letter}), 200
