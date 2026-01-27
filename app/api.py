"""
API Blueprint - All working endpoints from original app.py

Improvements in this version:
- Jobs persisted to Postgres (survives restarts, multi-worker safe)
- PubMed query caching (reduces latency and API calls)
- Schema validation for LLM outputs (prevents silent failures)
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
from flask import Blueprint, jsonify, request, send_file, current_app, has_request_context
from flask_login import login_required, current_user

# Database imports for job persistence
from app import db
from app.models import Job, PubMedCache

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


def validate_and_repair_analysis(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate analysis output and repair missing/malformed fields.
    
    This prevents silent failures when the model output drifts from expected schema.
    Returns a repaired dict that always has required fields.
    """
    if not obj or not isinstance(obj, dict):
        return dict(ANALYZE_SCHEMA)
    
    result = dict(ANALYZE_SCHEMA)
    result.update(obj)
    
    # Ensure diagnoses is a list of properly structured items
    if not isinstance(result.get("diagnoses"), list):
        result["diagnoses"] = []
    
    repaired_diagnoses = []
    for i, dx in enumerate(result.get("diagnoses", []), start=1):
        if not isinstance(dx, dict):
            continue
        # Ensure required fields
        repaired_dx = {
            "number": dx.get("number", i),
            "code": str(dx.get("code", "") or ""),
            "label": str(dx.get("label", "") or ""),
            "bullets": dx.get("bullets", []) if isinstance(dx.get("bullets"), list) else [],
            "severity": str(dx.get("severity", "") or ""),
            "urgency": str(dx.get("urgency", "") or ""),
            "refs": dx.get("refs", []) if isinstance(dx.get("refs"), list) else [],
        }
        if repaired_dx["label"]:  # Only include if has a label
            repaired_diagnoses.append(repaired_dx)
    result["diagnoses"] = repaired_diagnoses
    
    # Ensure plan is a list of properly structured items
    if not isinstance(result.get("plan"), list):
        result["plan"] = []
    
    repaired_plan = []
    for i, pl in enumerate(result.get("plan", []), start=1):
        if not isinstance(pl, dict):
            continue
        repaired_pl = {
            "number": pl.get("number", i),
            "title": str(pl.get("title", "") or ""),
            "bullets": pl.get("bullets", []) if isinstance(pl.get("bullets"), list) else [],
            "aligned_dx_numbers": pl.get("aligned_dx_numbers", []) if isinstance(pl.get("aligned_dx_numbers"), list) else [],
            "timeline": str(pl.get("timeline", "") or ""),
            "refs": pl.get("refs", []) if isinstance(pl.get("refs"), list) else [],
        }
        if repaired_pl["title"]:  # Only include if has a title
            repaired_plan.append(repaired_pl)
    result["plan"] = repaired_plan
    
    # Ensure references is a list
    if not isinstance(result.get("references"), list):
        result["references"] = []
    
    # Ensure warnings is a list of strings
    if not isinstance(result.get("warnings"), list):
        result["warnings"] = []
    result["warnings"] = [str(w) for w in result["warnings"] if w]
    
    # Ensure string fields are strings
    for field in ["provider_name", "patient_block", "summary_html", "chief_complaint"]:
        if not isinstance(result.get(field), str):
            result[field] = str(result.get(field, "") or "")
    
    return result


def analyze_prompt(note_text: str) -> str:
    excerpt = clamp_text(note_text, 16000)
    return f"""
You are an expert clinical documentation analyst with deep ophthalmology knowledge. Analyze this document thoroughly.

STEP 1 - DOCUMENT CLASSIFICATION
Determine what type of document this is:
- Clinical encounter note (exam, consultation, follow-up)
- Referral request (someone asking for a referral TO a specialist)
- Consultation report (specialist reporting BACK to referring doctor)
- Lab/imaging results
- Prescription or eyeglass Rx
- Insurance/authorization form
- Patient correspondence
- Other

STEP 2 - EXTRACT INFORMATION
Extract all clinical information accurately.

STEP 3 - CLINICAL REASONING (IMPORTANT)
Go BEYOND simple extraction:
- If exam findings suggest a diagnosis that wasn't explicitly listed, note it in suggested_diagnoses
- If a finding is mentioned but not addressed in the plan, note it in unaddressed_findings
- If clinical values are concerning, add to warnings
- Cross-reference findings with standard clinical guidelines mentally

Output VALID JSON only:
{{
  "document_type": "string - one of: encounter_note, referral_request, consultation_report, lab_results, prescription, insurance_form, correspondence, other",
  "provider_name": "string - the authoring/sending clinician name and credentials",
  "provider_clinic": "string - clinic or practice name if mentioned",
  "patient_block": "string - patient demographics with <br> line breaks: name, DOB/Age, Sex, PHN/MRN, phone, address. MUST include DOB or Age if available.",
  "summary_html": "string - EXACTLY 4 sections, NO MORE. Format: <p><b>Visit Context:</b> Reason for visit, relevant history, referral source.</p><p><b>Examination:</b></p><ul><li>VA: OD [value], OS [value]</li><li>IOP: OD [value] mmHg, OS [value] mmHg</li><li>Pupils: [all pupil findings]</li><li>Anterior segment: [lids, conjunctiva, cornea, AC, iris, lens - include ALL findings like LPI status, NSC grade, etc.]</li><li>Posterior segment: [vitreous, macula, vessels, periphery, C/D ratio - include ALL findings]</li><li>Imaging: [OCT, VF, photos, Optomap findings if any]</li></ul><p><b>Key Findings:</b> 1-2 sentences highlighting what is clinically significant. Do NOT repeat measurements.</p><p><b>Clinical Impression:</b> Brief synthesis. Do NOT list diagnoses here. STOP HERE - do not add any more sections.</p>",
  "chief_complaint": "string - main reason for visit or referral in one sentence",
  "diagnoses": [
    {{
      "number": 1,
      "code": "string - ICD code if available",
      "label": "string - diagnosis with laterality/severity",
      "bullets": ["key findings supporting this diagnosis"],
      "severity": "string - mild/moderate/severe/not specified",
      "urgency": "string - routine/soon/urgent/emergent"
    }}
  ],
  "suggested_diagnoses": [
    {{
      "label": "string - potential diagnosis suggested by findings",
      "supporting_findings": ["findings that suggest this"],
      "reasoning": "string - why this should be considered",
      "confidence": "string - low/medium/high"
    }}
  ],
  "plan": [
    {{
      "number": 1,
      "title": "string - action item title",
      "bullets": ["specific details"],
      "aligned_dx_numbers": [1],
      "timeline": "string - when this should happen"
    }}
  ],
  "unaddressed_findings": [
    {{
      "finding": "string - the finding that wasn't addressed",
      "potential_significance": "string - why this might matter",
      "suggested_action": "string - what could be done"
    }}
  ],
  "referral_info": {{
    "is_referral": true/false,
    "referral_direction": "string - 'requesting' or 'responding'",
    "referring_to": "string - specialist/subspecialty being referred to",
    "referring_from": "string - who is making the referral",
    "reason_for_referral": "string - specific reason",
    "requested_service": "string - what they want done"
  }},
  "clinical_values": {{
    "visual_acuity_od": "string",
    "visual_acuity_os": "string",
    "iop_od": "string",
    "iop_os": "string",
    "refraction": "string",
    "other_measurements": ["string - any other significant measurements"]
  }},
  "exam_findings": [
    {{
      "category": "string - e.g., Visual Acuity, Intraocular Pressure, Slit Lamp, Fundus, etc.",
      "findings": [
        {{
          "label": "string - finding name with laterality if applicable",
          "value": "string - the measurement or observation",
          "abnormal": "boolean - true if outside normal range"
        }}
      ]
    }}
  ],
  "warnings": ["any critical findings or red flags that need immediate attention"],
  "clinical_pearls": ["brief insights that might help the receiving clinician"],
  "follow_up": "string - recommended follow-up timing"
}}

CRITICAL FORMATTING RULES FOR summary_html:
1. EXACTLY 4 SECTIONS ONLY: Visit Context, Examination, Key Findings, Clinical Impression - NOTHING ELSE
2. DO NOT add a second Examination section after Clinical Impression - this is a common error
3. The ONE Examination section must be COMPREHENSIVE - include ALL exam findings in the bullet list
4. Each measurement (VA, IOP, etc.) appears ONCE in the Examination bullets, nowhere else
5. Key Findings describes significance in words, does NOT repeat numbers
6. Clinical Impression synthesizes meaning, does NOT list diagnoses or add more exam data
7. After Clinical Impression, STOP - do not add any more content to summary_html
8. For patient_block, ALWAYS extract age or DOB if present
9. exam_findings is a SEPARATE field for structured data - do NOT dump it into summary_html

CLINICAL REASONING RULES:
1. Extract facts explicitly stated in the document
2. For suggested_diagnoses: Only suggest if there's reasonable clinical evidence (e.g., RPE changes might suggest early AMD even if not explicitly diagnosed)
3. For unaddressed_findings: Flag findings mentioned but with no corresponding plan item
4. For warnings: Include values outside normal range (IOP > 21, VA worse than 20/40, etc.)
5. For clinical_pearls: Add helpful context (e.g., "LPI patent suggests prior angle closure episode")
6. Be conservative with suggestions - mark confidence appropriately
7. Never make up findings - only analyze what's actually documented

Document to analyze:
{excerpt}
""".strip()


def extract_patient_age(patient_block: str) -> Optional[int]:
    """Extract patient age from patient_block text.
    
    Looks for patterns like:
    - DOB: 1965-05-12 (calculates age)
    - Age: 58
    - 58 y/o, 58yo, 58 years old
    - (58) after name
    """
    if not patient_block:
        return None
    
    text = patient_block.lower()
    
    # Try explicit age patterns first
    age_patterns = [
        r'\bage[:\s]+(\d{1,3})\b',  # Age: 58
        r'(\d{1,3})\s*(?:y/?o|years?\s*old|yr)',  # 58 y/o, 58yo, 58 years old
        r'\((\d{1,3})\)',  # (58) - age in parentheses
    ]
    
    for pattern in age_patterns:
        match = re.search(pattern, text)
        if match:
            age = int(match.group(1))
            if 0 < age < 120:  # Sanity check
                return age
    
    # Try to calculate from DOB
    dob_patterns = [
        r'dob[:\s]+(\d{4})[-/](\d{1,2})[-/](\d{1,2})',  # DOB: 1965-05-12
        r'dob[:\s]+(\d{1,2})[-/](\d{1,2})[-/](\d{4})',  # DOB: 05/12/1965
        r'born[:\s]+(\d{4})[-/](\d{1,2})[-/](\d{1,2})',  # Born: 1965-05-12
    ]
    
    for pattern in dob_patterns:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            try:
                if len(groups[0]) == 4:  # YYYY-MM-DD
                    year = int(groups[0])
                else:  # MM/DD/YYYY
                    year = int(groups[2])
                current_year = datetime.now().year
                age = current_year - year
                if 0 < age < 120:
                    return age
            except (ValueError, IndexError):
                continue
    
    return None


def is_pediatric_reference(title: str) -> bool:
    """Check if a reference title indicates pediatric content."""
    title_lower = (title or "").lower()
    pediatric_keywords = [
        'pediatric', 'paediatric', 'child', 'children', 'infant', 
        'neonatal', 'neonate', 'newborn', 'adolescent', 'juvenile',
        'retinopathy of prematurity', 'rop', 'amblyopia', 'strabismus',
        'congenital', 'childhood'
    ]
    return any(kw in title_lower for kw in pediatric_keywords)


def pubmed_fetch_for_terms(terms: List[str], max_items: int = 12, patient_age: Optional[int] = None) -> List[Dict[str, str]]:
    uniq_terms: List[str] = []
    for t in (terms or []):
        t = (t or "").strip()
        if t and t.lower() not in [x.lower() for x in uniq_terms]:
            uniq_terms.append(t)

    if not uniq_terms:
        return []
    
    # Check cache first (significantly reduces latency for repeat queries)
    try:
        cached = PubMedCache.get_cached(uniq_terms)
        if cached:
            return cached
    except Exception:
        pass  # Cache check failed silently - proceed with API call

    blob = " ".join(uniq_terms).lower()
    
    # Determine if patient is adult (age >= 18) - filter out pediatric papers
    is_adult = patient_age is not None and patient_age >= 18
    is_pediatric_patient = patient_age is not None and patient_age < 18

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
        # Only include pediatric queries if patient is actually pediatric or age unknown
        if any(k in b for k in ["strabismus", "amblyopia", "esotropia", "exotropia"]):
            if is_pediatric_patient or patient_age is None:
                q += ["amblyopia preferred practice pattern", "strabismus clinical practice guideline"]
            else:
                q += ["adult strabismus guideline", "diplopia adult management"]
        if any(k in b for k in ["pediatric", "paediatric", "child", "infant"]) and not is_adult:
            q += ["pediatric eye evaluations preferred practice pattern", "retinopathy of prematurity guideline"]
        if any(k in b for k in ["optic neuritis", "papilledema", "neuro", "visual field defect"]):
            q += ["optic neuritis guideline", "papilledema evaluation guideline"]
        if any(k in b for k in ["retina", "macular", "amd", "diabetic retinopathy", "retinal detachment", "uveitis"]):
            q += ["diabetic retinopathy preferred practice pattern", "age related macular degeneration preferred practice pattern"]
        return q

    canonical_queries = add_queries_for_subspecialty(blob)
    case_queries: List[str] = []
    for term in uniq_terms[:8]:
        # Add adult filter to queries if patient is adult
        if is_adult:
            case_queries.append(f"({term}) ophthalmology adult")
            case_queries.append(f"({term}) (guideline OR consensus OR systematic review) NOT pediatric NOT children")
        else:
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
        num = 1
        for pid in pmids:
            if len(out) >= max_items:
                break
            item = result.get(pid) or {}
            title = (item.get("title") or "").strip().rstrip(".")
            
            # Filter out pediatric papers for adult patients
            if is_adult and is_pediatric_reference(title):
                continue
                
            source = (item.get("source") or "").strip()
            pubdate = (item.get("pubdate") or "").strip()
            authors = item.get("authors") or []
            first_author = (authors[0].get("name") if authors else "") or ""
            citation = " ".join([x for x in [first_author, title, source, pubdate] if x]).strip()
            out.append({
                "number": str(num),
                "pmid": pid,
                "citation": citation,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                "source": "PubMed",
            })
            num += 1
        
        # Cache the results for future queries
        if out:
            try:
                PubMedCache.set_cached(uniq_terms, out)
            except Exception:
                pass  # Cache write failed silently
        
        return out
    except Exception:
        return []


def canonical_reference_pool(labels, patient_age: Optional[int] = None):
    """Build a pool of authoritative references based on diagnoses.
    
    Prioritizes:
    - AAO Preferred Practice Patterns
    - European/International society guidelines
    - Cochrane reviews
    - Landmark clinical trials
    - NEJM, Lancet, JAMA Ophthalmology key papers
    
    Filters out pediatric references if patient is adult (age >= 18).
    """
    blob = " ".join([str(x or "") for x in (labels or [])]).lower()
    pool = []
    
    is_adult = patient_age is not None and patient_age >= 18
    is_pediatric_patient = patient_age is not None and patient_age < 18

    def add(pmid, citation, url="", source=""):
        pool.append({
            "pmid": (pmid or ""),
            "citation": (citation or ""),
            "url": (url or ""),
            "source": (source or ""),
        })

    # DRY EYE / OCULAR SURFACE
    if any(k in blob for k in ["dry eye", "meibomian", "mgd", "blepharitis", "ocular surface", "rosacea", "tear"]):
        add("41005521", "TFOS DEWS III: Executive Summary. Am J Ophthalmol. 2025.", "https://pubmed.ncbi.nlm.nih.gov/41005521/", "TFOS Guideline")
        add("28797892", "TFOS DEWS II Report Executive Summary. Ocul Surf. 2017.", "https://pubmed.ncbi.nlm.nih.gov/28797892/", "TFOS Guideline")
        add("", "TFOS DEWS III Reports Hub - Complete Guidelines", "https://www.tearfilm.org/paginades-tfos_dews_iii/7399_7239/eng/", "TFOS")
        add("28736335", "TFOS DEWS II Management and Therapy Report. Ocul Surf. 2017.", "https://pubmed.ncbi.nlm.nih.gov/28736335/", "TFOS Guideline")

    # MYOPIA
    if "myopia" in blob:
        add("30817826", "International Myopia Institute (IMI) White Papers. Invest Ophthalmol Vis Sci. 2019.", "https://pubmed.ncbi.nlm.nih.gov/30817826/", "IMI Consensus")
        add("", "IMI Clinical Guidelines - Myopia Management", "https://myopiainstitute.org/", "IMI")

    # GLAUCOMA
    if any(k in blob for k in ["glaucoma", "intraocular pressure", "iop", "ocular hypertension", "optic nerve", "cupping"]):
        add("34933745", "Primary Open-Angle Glaucoma PPP. Ophthalmology. 2021.", "https://pubmed.ncbi.nlm.nih.gov/34933745/", "AAO PPP")
        add("34675001", "European Glaucoma Society Terminology and Guidelines, 5th Ed. Br J Ophthalmol. 2021.", "https://pubmed.ncbi.nlm.nih.gov/34675001/", "EGS Guideline")
        add("", "AAO PPP: Primary Open-Angle Glaucoma", "https://www.aao.org/education/preferred-practice-pattern/primary-open-angle-glaucoma-ppp", "AAO PPP")
        add("19643495", "Ocular Hypertension Treatment Study. Arch Ophthalmol. 2002.", "https://pubmed.ncbi.nlm.nih.gov/12049575/", "Landmark Trial")

    # DIABETIC EYE DISEASE
    if any(k in blob for k in ["diabetic retinopathy", "diabetes", "diabetic macular", "dme"]):
        add("", "AAO PPP: Diabetic Retinopathy", "https://www.aao.org/education/preferred-practice-pattern/diabetic-retinopathy-ppp", "AAO PPP")
        add("26044954", "Diabetic Retinopathy PPP. Ophthalmology. 2020.", "https://pubmed.ncbi.nlm.nih.gov/31757496/", "AAO PPP")
        add("", "ADA Standards of Care in Diabetes - Eye Care", "https://diabetesjournals.org/care", "ADA Guideline")
        add("25903328", "DRCR.net Protocol T - Anti-VEGF for DME. NEJM. 2015.", "https://pubmed.ncbi.nlm.nih.gov/25692915/", "Landmark Trial")

    # AMD
    if any(k in blob for k in ["macular degeneration", "age related macular", "amd", "armd", "drusen", "choroidal neovascularization", "cnv"]):
        add("39918524", "Age-Related Macular Degeneration PPP. Ophthalmology. 2025.", "https://pubmed.ncbi.nlm.nih.gov/39918524/", "AAO PPP")
        add("", "AAO PPP: Age-Related Macular Degeneration", "https://www.aao.org/education/preferred-practice-pattern/age-related-macular-degeneration-ppp", "AAO PPP")
        add("11594942", "AREDS Report No. 8 - Antioxidant Supplementation. Arch Ophthalmol. 2001.", "https://pubmed.ncbi.nlm.nih.gov/11594942/", "Landmark Trial")
        add("23644932", "AREDS2 - Lutein/Zeaxanthin. JAMA. 2013.", "https://pubmed.ncbi.nlm.nih.gov/23644932/", "Landmark Trial")

    # KERATOCONUS / ECTASIA
    if any(k in blob for k in ["keratoconus", "ectasia", "corneal ectasia", "corneal thinning"]):
        add("26253489", "Global Consensus on Keratoconus and Ectatic Diseases. Cornea. 2015.", "https://pubmed.ncbi.nlm.nih.gov/26253489/", "Global Consensus")
        add("", "AAO PPP: Corneal Ectasia", "https://www.aao.org/education/preferred-practice-pattern", "AAO PPP")

    # CORNEA / KERATITIS
    if any(k in blob for k in ["cornea", "keratitis", "corneal ulcer", "corneal infection"]):
        add("", "AAO PPP: Bacterial Keratitis", "https://www.aao.org/education/preferred-practice-pattern/bacterial-keratitis-ppp", "AAO PPP")
        add("26253489", "Global Consensus on Keratoconus. Cornea. 2015.", "https://pubmed.ncbi.nlm.nih.gov/26253489/", "Global Consensus")

    # UVEITIS
    if "uveitis" in blob:
        add("16490958", "Standardization of Uveitis Nomenclature (SUN). Am J Ophthalmol. 2005.", "https://pubmed.ncbi.nlm.nih.gov/16490958/", "SUN Consensus")
        add("", "AAO PPP: Uveitis", "https://www.aao.org/education/preferred-practice-pattern", "AAO PPP")

    # CATARACT
    if "cataract" in blob:
        add("34780842", "Cataract in the Adult Eye PPP. Ophthalmology. 2022.", "https://pubmed.ncbi.nlm.nih.gov/34780842/", "AAO PPP")
        add("", "AAO PPP: Cataract in the Adult Eye", "https://www.aao.org/education/preferred-practice-pattern/cataract-in-adult-eye-ppp", "AAO PPP")
        add("", "European Society of Cataract and Refractive Surgeons Guidelines", "https://www.escrs.org/", "ESCRS")

    # STRABISMUS / AMBLYOPIA - filter for adult vs pediatric
    if any(k in blob for k in ["strabismus", "amblyopia", "esotropia", "exotropia", "diplopia"]):
        if is_adult:
            # For adults, focus on adult strabismus/diplopia resources
            add("", "AAO EyeWiki: Adult Strabismus", "https://eyewiki.aao.org/Adult_Strabismus", "AAO EyeWiki")
            add("", "AAO PPP: Adult Strabismus", "https://www.aao.org/education/preferred-practice-pattern", "AAO PPP")
        else:
            # For pediatric patients or unknown age, include pediatric resources
            add("", "AAO PPP: Amblyopia", "https://www.aao.org/education/preferred-practice-pattern/amblyopia-ppp", "AAO PPP")
            add("", "AAO PPP: Esotropia and Exotropia", "https://www.aao.org/education/preferred-practice-pattern/esotropia-exotropia-ppp", "AAO PPP")
            add("15545803", "PEDIG - Amblyopia Treatment Studies", "https://pubmed.ncbi.nlm.nih.gov/15545803/", "Landmark Trial")

    # PEDIATRIC - only include if patient is actually pediatric
    if any(k in blob for k in ["pediatric", "paediatric", "child", "infant", "rop"]) and not is_adult:
        add("", "AAO PPP: Pediatric Eye Evaluations", "https://www.aao.org/education/preferred-practice-pattern/pediatric-eye-evaluations-ppp", "AAO PPP")
        add("", "AAO PPP: Retinopathy of Prematurity", "https://www.aao.org/education/preferred-practice-pattern/retinopathy-of-prematurity-ppp", "AAO PPP")

    # NEURO-OPHTHALMOLOGY
    if any(k in blob for k in ["optic neuritis", "papilledema", "neuro", "visual field", "third nerve", "fourth nerve", "sixth nerve", "cranial nerve"]):
        add("", "AAO EyeWiki: Optic Neuritis", "https://eyewiki.aao.org/Optic_Neuritis", "AAO EyeWiki")
        add("", "AAO EyeWiki: Papilledema", "https://eyewiki.aao.org/Papilledema", "AAO EyeWiki")
        add("16105882", "Optic Neuritis Treatment Trial - 15 Year Follow-up. Ophthalmology. 2008.", "https://pubmed.ncbi.nlm.nih.gov/18675697/", "Landmark Trial")

    # RETINAL DETACHMENT / VITREOUS
    if any(k in blob for k in ["retinal detachment", "vitreous", "floaters", "pvd", "posterior vitreous"]):
        add("", "AAO PPP: Posterior Vitreous Detachment, Retinal Breaks, and Lattice Degeneration", "https://www.aao.org/education/preferred-practice-pattern", "AAO PPP")
        add("28284692", "Incidence of Retinal Detachment Following PVD. JAMA Ophthalmol. 2017.", "https://pubmed.ncbi.nlm.nih.gov/28284692/", "Clinical Study")

    # REFRACTIVE
    if any(k in blob for k in ["myopia", "hyperopia", "astigmatism", "presbyopia", "refractive"]):
        add("", "AAO PPP: Refractive Errors and Refractive Surgery", "https://www.aao.org/education/preferred-practice-pattern", "AAO PPP")

    return pool[:12]  # Return top 12 most relevant


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
    out_lang_line = f"Write the letter in {out_lang}." if out_lang and out_lang.lower() not in {"auto", "match", "match input"} else ""
    return f"""
You are a senior clinician writing a professional referral or report letter. Your goal is to communicate effectively while building collegial relationships.

Output VALID JSON only with this schema:
{{
  "letter_plain": "string - the complete letter in plain text format",
  "letter_html": "string - the letter formatted with HTML tags"
}}

TONE AND RELATIONSHIP:
- Write as one clinician to another - collegial, respectful, and collaborative
- Express genuine appreciation for their expertise and time
- Signal willingness to comanage and collaborate
- If recipient_type equals "Patient", write in warm, accessible language while maintaining professionalism
- For physicians, use precise medical terminology but remain personable

SPECIAL REQUESTS HANDLING:
The special_requests field is an INTENT SIGNAL from the referring provider. 
- NEVER quote it verbatim or include it as a section
- WEAVE the intent naturally into the referral narrative and closing
- Let it inform what you emphasize without explicitly stating it

Language:
{out_lang_line}

Clinic context:
clinic_name: {os.getenv("CLINIC_NAME","")}
clinic_address: {os.getenv("CLINIC_ADDRESS","")}
clinic_phone: {os.getenv("CLINIC_PHONE","")}

LETTER STRUCTURE:

1. HEADER (one item per line):
To: <recipient>
From: <authoring provider>
Date: <current date>

Patient: <full name>
DOB: <date> (<age>)
Sex: <sex>
PHN: <phn if available>
Phone: <phone if available>

{reason_label}: <diagnosis plus referral focus, written naturally>

2. SALUTATION:
Dear <recipient name or "Colleague">,

3. OPENING PARAGRAPH (relationship-building):
- Start with genuine appreciation: "Thank you for seeing..." or "I would be grateful for your expertise with..."
- Introduce the patient with context: name, age, chief complaint
- State the referral reason naturally, incorporating the requested service
- Add urgency context if relevant
- This should read like a real letter between colleagues, not a form

4. CLINICAL CONTENT:
Use headings for:
- Exam findings (include objective measurements, key negatives, imaging results)
- Assessment (problem list with laterality and severity)
- Plan (current management and what you're asking them to do)

5. CLOSING PARAGRAPH (collaboration signal):
- Express appreciation for seeing the patient
- Request their impressions and recommendations
- Signal openness to comanagement
- End with "Kind regards," ONLY - do not add the provider name after (signature will be added separately)

RULES:
- Do NOT include Evidence, Disclaimer, or References sections
- Do NOT include citation numbers [1] in the letter body
- Do NOT repeat the provider name after "Kind regards,"
- Keep exam findings detailed but relevant to the referral
- Make the letter feel personal and collegial, not templated

Form:
{json.dumps(form, ensure_ascii=False)}

Analysis:
{json.dumps(analysis, ensure_ascii=False)}
""".strip()


def finalize_signoff(letter_plain: str, provider_name: str, has_signature: bool) -> str:
    txt = (letter_plain or "").rstrip()
    if not txt:
        return ""
    
    # If we have a signature, remove everything after "Kind regards" 
    # (the PDF export will add signature + name)
    if has_signature:
        lines = txt.splitlines()
        result_lines = []
        for line in lines:
            lower = line.strip().lower()
            if lower.startswith("kind regards"):
                result_lines.append("Kind regards,")
                break  # Stop here - don't include anything after
            result_lines.append(line)
        return "\n".join(result_lines).rstrip()
    
    # No signature - ensure proper signoff with provider name
    lines = txt.splitlines()
    if lines:
        last = lines[-1].strip()
        prov = (provider_name or "").strip()
        if prov and last.lower() == prov.lower():
            lines = lines[:-1]
    txt = "\n".join(lines).rstrip()
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
    """
    Persist job state to Postgres (primary) with in-memory cache (secondary).
    
    This ensures jobs survive worker restarts and work across multiple workers.
    """
    _ensure_job_dir()
    
    # Update in-memory cache first (for fast reads during processing)
    with JOBS_LOCK:
        job_dict = JOBS.get(job_id) or {}
        job_dict.update(updates)
        JOBS[job_id] = job_dict
    
    # Persist to Postgres
    try:
        with current_app.app_context():
            job = Job.query.get(job_id)
            if not job:
                # Create new job record
                job = Job(id=job_id)
                # Set user/org if authenticated (request context only)
                if has_request_context():
                    try:
                        if current_user and getattr(current_user, 'is_authenticated', False):
                            job.user_id = current_user.id
                            job.organization_id = current_user.organization_id
                    except Exception:
                        pass
                db.session.add(job)
            # Update job from dict
            job.update_from_dict(updates)
            db.session.commit()
    except Exception:
        # Postgres failed (likely no app context in background thread)
        # Fall back to file storage silently
        path = _job_path(job_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(job_dict, f, ensure_ascii=False)
        except Exception:
            pass


# Always persist to file storage (cross-worker fallback and debugging)
path = _job_path(job_id)
try:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job_dict, f, ensure_ascii=False)
except Exception:
    pass

    # Also persist to S3 for disaster recovery (if enabled)
    if job_s3_enabled():
        bucket = os.getenv("AWS_S3_BUCKET", "").strip()
        try:
            s3, _ = aws_clients()
        except Exception:
            s3 = None
        if s3 is not None:
            body = json.dumps(job_dict, ensure_ascii=False).encode("utf-8")
            for key in job_s3_key_fallbacks(job_id):
                try:
                    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
                    break
                except Exception:
                    continue


def get_job(job_id: str) -> Dict[str, Any]:
    """
    Get job state from Postgres (primary) with fallbacks to cache, file, and S3.
    
    Priority: in-memory cache → Postgres → file → S3
    """
    _ensure_job_dir()
    
    # Check Postgres first (authoritative across workers)
    # (cache is a best-effort optimization and can be stale or empty on other workers)
    
    # Check Postgres
    try:
        with current_app.app_context():
            job = Job.query.get(job_id)
            if job:
                job_dict = job.to_dict()
                # Update cache
                with JOBS_LOCK:
                    JOBS[job_id] = job_dict
                return job_dict
    except Exception:
        # Postgres failed (likely no app context in background thread)
        pass
    
    # Fall back to file storage
    path = _job_path(job_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            job_dict = json.load(f) or {}
        if isinstance(job_dict, dict):
            with JOBS_LOCK:
                JOBS[job_id] = job_dict
            return dict(job_dict)
    except Exception:
        pass

    
# Final fallback: in-memory cache (same-worker only)
with JOBS_LOCK:
    if job_id in JOBS:
        return dict(JOBS.get(job_id) or {})

# Fall back to S3
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
                    job_dict = json.loads(body.decode("utf-8", errors="ignore")) or {}
                    if isinstance(job_dict, dict):
                        with JOBS_LOCK:
                            JOBS[job_id] = job_dict
                        try:
                            with open(path, "w", encoding="utf-8") as f:
                                json.dump(job_dict, f, ensure_ascii=False)
                        except Exception:
                            pass
                        return dict(job_dict)
                except Exception:
                    continue
    return {}


def run_analysis_job(job_id: str, note_text: str) -> None:
    # Stage 1: Starting analysis
    set_job(job_id, status="processing", stage="analyzing", stage_label="Analyzing document...", progress=15, updated_at=now_utc_iso(), heartbeat_at=now_utc_iso())
    
    obj, err = llm_json(analyze_prompt(note_text))
    
    # Stage 2: Structuring results
    set_job(job_id, stage="structuring", stage_label="Structuring results...", progress=50, heartbeat_at=now_utc_iso())
    
    if err or not obj:
        set_job(job_id, status="error", error=err or "Analysis failed", updated_at=now_utc_iso())
        return

    # Validate and repair the LLM output to ensure consistent schema
    analysis = validate_and_repair_analysis(obj)

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

    # Extract patient age for reference filtering
    patient_age = extract_patient_age(pb)
    analysis["patient_age"] = patient_age

    # Stage 3: Fetching references
    set_job(job_id, stage="references", stage_label="Fetching references...", progress=65, heartbeat_at=now_utc_iso())
    
    terms = []
    for dx in analysis.get("diagnoses") or []:
        if isinstance(dx, dict):
            label = (dx.get("label") or "").strip()
            if label:
                terms.append(label)
    # Pass patient_age to filter out irrelevant pediatric papers for adult patients
    references = pubmed_fetch_for_terms(terms, patient_age=patient_age)
    canonical = canonical_reference_pool([dx.get('label') for dx in (analysis.get('diagnoses') or []) if isinstance(dx, dict)], patient_age=patient_age)
    analysis['references'] = merge_references(references, canonical)

    # Stage 4: Assigning citations
    set_job(job_id, stage="citations", stage_label="Assigning citations...", progress=80, heartbeat_at=now_utc_iso())

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

    # Stage 5: Complete
    set_job(job_id, status="complete", stage="complete", stage_label="Complete", progress=100, data=analysis, updated_at=now_utc_iso())


def run_analysis_upload_job(job_id: str, filename: str, data: bytes, force_ocr: bool = False) -> None:
    # Stage 0: Extracting text
    set_job(job_id, status="processing", stage="extracting", stage_label="Extracting text...", progress=5, updated_at=now_utc_iso())
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
                # Stage: OCR in progress
                set_job(job_id, stage="ocr", stage_label="Running OCR...", progress=8, heartbeat_at=now_utc_iso())
                ocr_attempted = True
                ocr_text, ocr_err = ocr_pdf_bytes(data)
                if ocr_err:
                    set_job(job_id, status="error", error=ocr_err, updated_at=now_utc_iso())
                    return
                if ocr_text:
                    note_text = ocr_text

            set_job(job_id, heartbeat_at=now_utc_iso())

        elif name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            set_job(job_id, stage="ocr", stage_label="Running OCR on image...", progress=8, heartbeat_at=now_utc_iso())
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
        status="processing",
        stage="received",
        stage_label="Received file",
        progress=1,
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
    set_job(job_id, status="waiting", stage="received", stage_label="Received", progress=0, updated_at=now_utc_iso())
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

    def signature_slug(prov_name: str) -> str:
        s = (prov_name or "").strip().lower()
        s = re.sub(r"\b(dr\.?|md|od|mba)\b", "", s)
        s = re.sub(r"[^a-z0-9]+", "_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s

    def find_signature_image(prov_name: str) -> Optional[str]:
        base_dir = os.getenv("SIGNATURE_DIR", "static/signatures")
        # Try multiple possible locations
        possible_dirs = [
            # Relative to api.py file (app/static/signatures)
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "signatures"),
            # Relative to project root (app/static/signatures)
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "signatures"),
            # Environment-specified directory
            base_dir if os.path.isabs(base_dir) else os.path.join(os.path.dirname(os.path.abspath(__file__)), base_dir),
        ]
        
        slug = signature_slug(prov_name)
        if not slug:
            return None
        
        for abs_dir in possible_dirs:
            if not os.path.isdir(abs_dir):
                continue
            for ext in (".png", ".jpg", ".jpeg"):
                cand = os.path.join(abs_dir, slug + ext)
                if os.path.exists(cand):
                    return cand
        return None

    # Get letterhead: client upload > static letterhead.png
    lh_override = data_url_to_tempfile(letterhead_data_url, "letterhead")
    lh_path = lh_override
    if not lh_path:
        static_lh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "letterhead.png")
        if os.path.exists(static_lh):
            lh_path = static_lh

    # Get signature: client upload > server lookup by provider name
    sig_override = data_url_to_tempfile(signature_data_url, "signature")
    sig_path_effective = sig_override or find_signature_image(provider_name)
    
    # If we have a signature, adjust text to not include provider name at end
    if sig_path_effective and os.path.exists(sig_path_effective):
        text_in = finalize_signoff(text_in, provider_name, True)

    styles = getSampleStyleSheet()
    # Compact styles for single-page fit
    base = ParagraphStyle(
        "base", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9.5, leading=12, spaceAfter=2, alignment=TA_JUSTIFY
    )
    head = ParagraphStyle(
        "head", parent=base, fontName="Helvetica-Bold",
        spaceBefore=6, spaceAfter=3, alignment=TA_LEFT
    )
    mono = ParagraphStyle(
        "mono", parent=base, fontName="Helvetica",
        fontSize=9.5, leading=11, alignment=TA_LEFT, spaceAfter=0
    )

    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def meaningful(v: str) -> bool:
        if not v:
            return False
        lv = v.strip().lower()
        return lv not in {"na", "n/a", "none", "unknown", ""}

    def emit_demographics(demo: dict) -> list:
        """Compact demographics layout - multiple fields per line"""
        lines = []
        # Line 1: Patient, DOB, Sex, PHN
        parts = []
        if meaningful(demo.get("patient")):
            parts.append(f"<b>Patient:</b> {esc(demo.get('patient'))}")
        if meaningful(demo.get("dob")):
            parts.append(f"<b>DOB:</b> {esc(demo.get('dob'))}")
        if meaningful(demo.get("sex")):
            parts.append(f"<b>Sex:</b> {esc(demo.get('sex'))}")
        if meaningful(demo.get("phn")):
            parts.append(f"<b>PHN:</b> {esc(demo.get('phn'))}")
        if parts:
            lines.append(Paragraph("  ".join(parts), mono))

        # Line 2: Phone, Email, Address
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

    # Add letterhead if available
    if lh_path and os.path.exists(lh_path):
        try:
            img = RLImage(lh_path)
            img.drawHeight = 45
            img.drawWidth = 480
            story.append(img)
            story.append(Spacer(1, 6))
        except Exception:
            pass

    raw_lines = text_in.splitlines()
    demo_keys = {"patient", "dob", "sex", "phn", "phone", "email", "address"}
    demo_data = {}
    demo_active = False
    demo_emitted = False
    in_signoff = False  # Track if we're past "Kind regards"
    provider_name_lower = provider_name.lower().strip()

    for raw in raw_lines:
        line = (raw or "").rstrip()
        if not line.strip():
            if demo_active and not demo_emitted:
                story.extend(emit_demographics(demo_data))
                demo_emitted = True
            story.append(Spacer(1, 6))
            continue

        lower = line.strip().lower()
        key = lower.split(":", 1)[0].strip() if ":" in lower else ""
        
        # Skip provider name if it appears after Kind regards (avoid duplicate)
        if in_signoff:
            # Skip if this line is just the provider name
            line_clean = re.sub(r'[,.\s]+', '', lower)
            prov_clean = re.sub(r'[,.\s]+', '', provider_name_lower)
            if line_clean == prov_clean or lower == provider_name_lower:
                continue
            # Also skip common name patterns
            if lower.startswith("dr.") or lower.startswith("dr "):
                if any(part in lower for part in provider_name_lower.split()):
                    continue

        # Collect demographics for compact display
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

        # Skip "Clinical Summary" heading
        if lower in {"clinical summary", "clinical summary:"}:
            continue

        # Reason for referral/report - styled prominently
        if lower.startswith("reason for referral") or lower.startswith("reason for report"):
            label = "Reason for Referral" if lower.startswith("reason for referral") else "Reason for Report"
            value = line.split(":", 1)[1].strip() if ":" in line else ""
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>{label}:</b> {esc(value)}", base))
            story.append(Spacer(1, 6))
            continue

        # Section headings
        if lower in {"exam findings", "exam findings:", "assessment", "assessment:", "plan", "plan:"}:
            title = line.strip().replace(":", "")
            story.append(Paragraph(f"<b>{esc(title)}</b>", head))
            continue

        # To/From/Date header lines
        if lower.startswith("to:") or lower.startswith("from:") or lower.startswith("date:"):
            try:
                k, v = line.split(":", 1)
                story.append(Paragraph(f"<b>{esc(k)}:</b> {esc(v.strip())}", mono))
            except Exception:
                story.append(Paragraph(esc(line), mono))
            continue

        # Salutation
        if lower.startswith("dear "):
            story.append(Spacer(1, 6))
            story.append(Paragraph(esc(line), base))
            story.append(Spacer(1, 4))
            continue

        # Signature block
        if lower.startswith("kind regards"):
            in_signoff = True
            story.append(Spacer(1, 8))
            story.append(Paragraph("Kind regards,", base))
            
            if sig_path_effective and os.path.exists(sig_path_effective):
                try:
                    sig = RLImage(sig_path_effective)
                    page_w = rl_letter[0]
                    max_width = int(page_w * 0.22)
                    max_height = 70
                    iw = float(sig.imageWidth)
                    ih = float(sig.imageHeight)
                    if iw > 0 and ih > 0:
                        scale = min(max_width / iw, max_height / ih)
                        sig.drawWidth = iw * scale
                        sig.drawHeight = ih * scale
                    story.append(Spacer(1, 4))
                    story.append(sig)
                    # Add provider name under signature
                    story.append(Spacer(1, 2))
                    story.append(Paragraph(esc(provider_name), base))
                except Exception:
                    story.append(Paragraph(esc(provider_name), base))
            else:
                # No signature image - just add provider name
                story.append(Paragraph(esc(provider_name), base))
            continue

        # Regular paragraph
        story.append(Paragraph(esc(line), base))

    # Emit any remaining demographics
    if demo_active and not demo_emitted:
        story.extend(emit_demographics(demo_data))

    doc = SimpleDocTemplate(
        out_path,
        pagesize=rl_letter,
        leftMargin=50,
        rightMargin=50,
        topMargin=45,
        bottomMargin=45,
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

def triage_fax_prompt(summary_html: str, patient_block: str, full_text: str = "", analysis: dict = None) -> str:
    """Prompt for triaging incoming fax/communication with intelligent reasoning"""
    context = full_text or summary_html or ""
    
    # Include more analysis data if available
    extra_context = ""
    if analysis:
        doc_type = analysis.get("document_type", "")
        referral_info = analysis.get("referral_info", {})
        diagnoses = analysis.get("diagnoses", [])
        chief_complaint = analysis.get("chief_complaint", "")
        provider_name = analysis.get("provider_name", "")
        provider_clinic = analysis.get("provider_clinic", "")
        
        if doc_type:
            extra_context += f"\nDocument type detected: {doc_type}"
        if chief_complaint:
            extra_context += f"\nChief complaint: {chief_complaint}"
        if provider_name:
            extra_context += f"\nSending provider: {provider_name}"
        if provider_clinic:
            extra_context += f"\nSending clinic: {provider_clinic}"
        if referral_info.get("is_referral"):
            extra_context += f"\nReferral direction: {referral_info.get('referral_direction', 'unknown')}"
            extra_context += f"\nReferring to: {referral_info.get('referring_to', '')}"
            extra_context += f"\nReason: {referral_info.get('reason_for_referral', '')}"
            extra_context += f"\nRequested service: {referral_info.get('requested_service', '')}"
        if diagnoses:
            dx_list = ", ".join([d.get("label", "") for d in diagnoses[:5] if d.get("label")])
            extra_context += f"\nDiagnoses: {dx_list}"
    
    return f"""
You are an expert medical office coordinator helping triage incoming faxes and communications for an ophthalmology clinic.

STEP 1: DOCUMENT CLASSIFICATION
Identify the document type:
- REFERRAL REQUEST: Another doctor asking us to see their patient (IMPORTANT: requires scheduling)
- CONSULTATION REPORT: A specialist sending us a report about our patient
- LAB/IMAGING RESULTS: Test results to be filed or reviewed
- INSURANCE/PRIOR AUTH: Authorization request or approval
- MEDICAL RECORDS REQUEST: Someone requesting patient records
- PRESCRIPTION REFILL: Request for medication/glasses Rx
- PATIENT CORRESPONDENCE: Letter from or about a patient
- MARKETING/SPAM: Promotional material (file/discard)
- OTHER: Miscellaneous correspondence

STEP 2: URGENCY ASSESSMENT
- URGENT: Same-day action needed (acute symptoms, critical results, time-sensitive auth)
- SOON: Within 1-3 days (routine referrals, pending results)
- ROUTINE: Standard processing (records requests, normal results)

STEP 3: ANALYZE CAREFULLY
{extra_context}

Patient information:
{patient_block}

Document content:
{context[:10000]}

STEP 4: GENERATE ACTIONABLE TASKS
Based on your analysis, create specific tasks for staff and doctors.

Output VALID JSON only:
{{
  "document_type": "string - REFERRAL_REQUEST, CONSULTATION_REPORT, LAB_RESULTS, INSURANCE, RECORDS_REQUEST, PRESCRIPTION, CORRESPONDENCE, MARKETING, OTHER",
  "urgency": "string - URGENT, SOON, ROUTINE",
  "from_provider": "string - name of sending doctor/organization",
  "from_clinic": "string - clinic/organization name",
  "from_fax": "string - fax number if visible",
  "regarding": "string - clear description: patient name + what this is about",
  "patient_name": "string - patient name if mentioned",
  "patient_dob": "string - DOB if mentioned",
  "reasoning": "string - brief explanation of your classification and why",
  "front_desk_tasks": [
    "string - specific actionable task with clear instruction"
  ],
  "doctor_tasks": [
    "string - specific actionable task requiring clinical decision"
  ],
  "key_clinical_info": "string - any critical clinical details the doctor should know immediately"
}}

TASK WRITING RULES:
1. Tasks must be specific and actionable (who, what, when)
2. Include patient name in tasks when known
3. For REFERRAL REQUESTS: First front desk task should be about scheduling
4. For REFERRAL REQUESTS: Doctor task should be about reviewing if they want to accept
5. For RESULTS: Note if normal vs abnormal
6. For INSURANCE: Note deadlines if visible
7. If document is unclear or illegible, note that in reasoning

EXAMPLE for a referral request:
{{
  "document_type": "REFERRAL_REQUEST",
  "urgency": "SOON",
  "from_provider": "Dr. Jane Smith, OD",
  "from_clinic": "Vision Care Associates",
  "regarding": "John Doe (DOB 05/12/1965) - Glaucoma referral for IOP management",
  "front_desk_tasks": [
    "Schedule new patient appointment for John Doe (DOB 05/12/1965) - glaucoma consultation",
    "Call Vision Care Associates at [fax number] to confirm receipt and get appointment scheduled",
    "Request previous records including OCT and visual fields"
  ],
  "doctor_tasks": [
    "Review referral from Dr. Smith for John Doe - elevated IOP OD 28, OS 26, suspicious optic nerves",
    "Determine appointment urgency based on IOP levels"
  ],
  "key_clinical_info": "IOP elevated: OD 28, OS 26. C/D ratio 0.7 OU. Patient on Latanoprost."
}}
""".strip()


def patient_letter_prompt(analysis: dict, form: dict = None) -> str:
    """Prompt for generating patient-friendly letter"""
    form = form or {}
    diagnoses = analysis.get("diagnoses", [])
    plan = analysis.get("plan", [])
    
    # Format diagnoses for the letter
    dx_summary = ""
    for dx in diagnoses[:5]:
        if isinstance(dx, dict) and dx.get("label"):
            dx_summary += f"- {dx.get('label')}\n"
    
    # Format plan items
    plan_summary = ""
    for p in plan[:5]:
        if isinstance(p, dict) and p.get("title"):
            plan_summary += f"- {p.get('title')}\n"
            for bullet in (p.get("bullets") or [])[:3]:
                plan_summary += f"  - {bullet}\n"
    
    # Extract form settings
    recipient_type = form.get("recipient_type", "Patient")
    to_whom = form.get("to_whom", "")
    from_doctor = form.get("from_doctor", "") or analysis.get("provider_name", "")
    reason = form.get("reason_for_referral", "")
    focus = form.get("referral_focus", "")
    special = form.get("special_requests", "")
    tone = form.get("tone", "warm")
    detail = form.get("detail", "standard")
    out_lang = form.get("output_language", "en")
    
    tone_instruction = {
        "warm": "Use warm, reassuring, empathetic language. The patient may be anxious.",
        "professional": "Use clear, professional language while remaining approachable.",
        "simple": "Use very simple, plain language. Avoid all medical jargon."
    }.get(tone, "Use warm, reassuring language.")
    
    detail_instruction = {
        "brief": "Keep the letter concise - 2-3 short paragraphs maximum.",
        "standard": "Provide a balanced level of detail - about 4-5 paragraphs.",
        "detailed": "Provide comprehensive detail with thorough explanations."
    }.get(detail, "Provide a balanced level of detail.")
    
    lang_map = {"en": "", "es": "Write the letter in Spanish.", "pt": "Write the letter in Portuguese.", "fr": "Write the letter in French."}
    lang_instruction = lang_map.get(out_lang, "")
    
    reason_section = f"REASON FOR LETTER: {reason}" if reason else ""
    focus_section = f"FOCUS/PURPOSE: {focus}" if focus else ""
    special_section = f"SPECIAL REQUESTS: {special}" if special else ""
    
    return f"""
You are a compassionate healthcare communicator writing a letter about a recent eye examination.

RECIPIENT TYPE: {recipient_type}
TO: {to_whom or 'the patient'}
FROM: {from_doctor}

PATIENT INFO:
{analysis.get('patient_block', '')}

CLINICAL SUMMARY:
{analysis.get('summary_html', '')[:4000]}

DIAGNOSES FOUND:
{dx_summary}

RECOMMENDED PLAN:
{plan_summary}

{reason_section}
{focus_section}
{special_section}

TONE: {tone_instruction}
DETAIL LEVEL: {detail_instruction}
{lang_instruction}

WRITING GUIDELINES:
1. Address the letter appropriately for {recipient_type}
2. Explain medical terms in plain English (e.g., "IOP" means "eye pressure")
3. If findings are normal, emphasize the good news
4. If there are concerns, explain them clearly but without undue alarm
5. List next steps clearly (appointments, medications, lifestyle changes)
6. Include when they should return for follow-up
7. Sign off appropriately from {from_doctor}

STRUCTURE:
- Opening: Reference their recent visit
- Summary: What was examined and found (in plain terms)
- What this means: Explain the significance simply
- Next steps: Clear action items
- Closing: Appropriate sign-off from {from_doctor}

Output VALID JSON only:
{{{{
  "letter": "string - the complete letter text with proper paragraph breaks"
}}}}
""".strip()

def insurance_letter_prompt(analysis: dict, form: dict = None) -> str:
    """Prompt for generating insurance/prior authorization letter"""
    form = form or {}
    diagnoses = analysis.get("diagnoses", [])
    plan = analysis.get("plan", [])
    
    # Extract form settings
    from_doctor = form.get("from_doctor", "") or analysis.get("provider_name", "")
    reason = form.get("reason_for_referral", "")
    focus = form.get("referral_focus", "")
    special = form.get("special_requests", "")
    detail = form.get("detail", "standard")
    out_lang = form.get("output_language", "en")
    
    # Format diagnoses with codes
    dx_formatted = ""
    for dx in diagnoses[:5]:
        if isinstance(dx, dict):
            code = dx.get("code", "")
            label = dx.get("label", "")
            bullets = dx.get("bullets", [])
            dx_formatted += f"- {code} {label}\n"
            for b in bullets[:3]:
                dx_formatted += f"  - {b}\n"
    
    # Format plan items
    plan_formatted = ""
    for p in plan[:5]:
        if isinstance(p, dict):
            plan_formatted += f"- {p.get('title', '')}\n"
            for b in (p.get("bullets") or [])[:3]:
                plan_formatted += f"  - {b}\n"
    
    detail_instruction = {
        "brief": "Keep the letter concise while covering all essential points.",
        "standard": "Provide thorough documentation with all relevant clinical details.",
        "detailed": "Provide exhaustive documentation with comprehensive clinical justification."
    }.get(detail, "Provide thorough documentation.")
    
    lang_map = {"en": "", "es": "Write the letter in Spanish.", "pt": "Write the letter in Portuguese.", "fr": "Write the letter in French."}
    lang_instruction = lang_map.get(out_lang, "")
    
    reason_section = f"SPECIFIC CONDITION/PROCEDURE: {reason}" if reason else ""
    focus_section = f"REQUESTED SERVICE: {focus}" if focus else ""
    special_section = f"ADDITIONAL NOTES: {special}" if special else ""
    
    return f"""
You are a medical documentation specialist writing a letter for insurance purposes (prior authorization, medical necessity, or appeal).

FROM PROVIDER: {from_doctor}

PATIENT INFO:
{analysis.get('patient_block', '')}

CLINICAL SUMMARY:
{analysis.get('summary_html', '')[:4000]}

DIAGNOSES (with ICD codes if available):
{dx_formatted}

TREATMENT PLAN:
{plan_formatted}

{reason_section}
{focus_section}
{special_section}

DETAIL LEVEL: {detail_instruction}
{lang_instruction}

LETTER REQUIREMENTS:
1. Use formal medical terminology appropriate for insurance review
2. Include ICD-10 codes when available
3. Clearly establish MEDICAL NECESSITY - why this treatment/procedure is required
4. Reference specific clinical findings (measurements, test results, exam findings)
5. Explain why alternative treatments are inadequate or have been tried
6. Include relevant history supporting the necessity
7. State the specific treatment/procedure being requested
8. Reference clinical guidelines or standard of care when applicable

STRUCTURE:
- Header: Date, To: Medical Review Department, Re: Prior Authorization / Medical Necessity
- Patient identification: Name, DOB, Insurance ID
- Opening: Purpose of letter and what is being requested
- Clinical history: Relevant background
- Current findings: Objective examination findings
- Diagnosis: With ICD codes
- Medical necessity statement: Why this treatment is required
- Treatment plan: What is being requested
- Supporting evidence: Guidelines, standards of care
- Closing: Request for approval, contact for questions
- Sign from {from_doctor}

PERSUASION TECHNIQUES:
- Lead with the most compelling clinical findings
- Use specific numbers (visual acuity, IOP, measurements)
- Reference progressive deterioration if applicable
- Cite impact on daily functioning / quality of life
- Note any failed conservative treatments

Output VALID JSON only:
{{{{
  "letter": "string - the complete formal letter text"
}}}}
""".strip()


@api_bp.route("/triage_fax", methods=["POST"])
@login_required
def triage_fax():
    """Triage an incoming fax/communication for front desk"""
    payload = request.get_json(silent=True) or {}
    analysis = payload.get("analysis") or {}
    
    summary_html = analysis.get("summary_html", "")
    patient_block = analysis.get("patient_block", "")
    
    # Pass full analysis for better context
    prompt = triage_fax_prompt(summary_html, patient_block, analysis=analysis)
    obj, err = llm_json(prompt, temperature=0.1)  # Lower temperature for more consistent output
    
    if err or not obj:
        return jsonify({"ok": False, "error": err or "Triage failed"}), 200
    
    return jsonify({
        "ok": True,
        "document_type": obj.get("document_type", ""),
        "urgency": obj.get("urgency", "ROUTINE"),
        "from_provider": obj.get("from_provider", obj.get("from", "")),
        "from_clinic": obj.get("from_clinic", ""),
        "from_fax": obj.get("from_fax", ""),
        "regarding": obj.get("regarding", ""),
        "patient_name": obj.get("patient_name", ""),
        "patient_dob": obj.get("patient_dob", ""),
        "reasoning": obj.get("reasoning", ""),
        "front_desk_tasks": obj.get("front_desk_tasks", []),
        "doctor_tasks": obj.get("doctor_tasks", []),
        "key_clinical_info": obj.get("key_clinical_info", ""),
        # Keep backwards compatibility
        "from": obj.get("from_provider", obj.get("from", ""))
    }), 200


@api_bp.route("/generate_assistant_letter", methods=["POST"])
@login_required
def generate_assistant_letter():
    """Generate patient or insurance letter from analysis"""
    payload = request.get_json(silent=True) or {}
    analysis = payload.get("analysis") or {}
    letter_type = payload.get("letter_type", "patient")
    
    # Extract form fields
    form = {
        "recipient_type": payload.get("recipient_type", "Patient"),
        "to_whom": payload.get("to_whom", ""),
        "from_doctor": payload.get("from_doctor", ""),
        "reason_for_referral": payload.get("reason_for_referral", ""),
        "referral_focus": payload.get("referral_focus", ""),
        "special_requests": payload.get("special_requests", ""),
        "tone": payload.get("tone", "warm"),
        "detail": payload.get("detail", "standard"),
        "output_language": payload.get("output_language", "en"),
    }
    
    if letter_type == "insurance":
        prompt = insurance_letter_prompt(analysis, form)
    else:
        prompt = patient_letter_prompt(analysis, form)
    
    obj, err = llm_json(prompt, temperature=0.3)
    
    if err or not obj:
        return jsonify({"ok": False, "error": err or "Letter generation failed"}), 200
    
    letter = obj.get("letter", "")
    if not letter:
        return jsonify({"ok": False, "error": "Empty letter generated"}), 200
    
    return jsonify({"ok": True, "letter": letter}), 200
