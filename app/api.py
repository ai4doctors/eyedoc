"""
Maneiro.ai API Routes
Includes v2026.6+ improvements: progress stages, schema validation, specialty handling
"""
import os
import io
import re
import json
import uuid
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from flask_wtf.csrf import generate_csrf

from app.models import db, Job, JobStatus, JobType, AuditLog
from app.auth import usage_limit_check, log_audit

api_bp = Blueprint("api", __name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

def feature_enabled(name: str, default: bool = True) -> bool:
    """Check if feature flag is enabled"""
    env_key = f"FEATURE_{name.upper()}"
    val = os.environ.get(env_key, str(default)).lower()
    return val in ("1", "true", "yes", "on")

# Specialty configurations
SPECIALTIES = {
    "auto": {"name": "Auto-detect", "prompt_modifier": ""},
    "ophthalmology": {
        "name": "Ophthalmology",
        "prompt_modifier": "Focus on ophthalmic findings: visual acuity, IOP, slit lamp, fundus, OCT, visual fields. Use standard ophthalmic terminology."
    },
    "primary_care": {
        "name": "Primary Care",
        "prompt_modifier": "Focus on general medical assessment, preventive care, chronic disease management."
    },
    "cardiology": {
        "name": "Cardiology",
        "prompt_modifier": "Focus on cardiovascular findings: ECG, echo, stress testing, risk stratification."
    },
    "dermatology": {
        "name": "Dermatology",
        "prompt_modifier": "Focus on skin findings: morphology, distribution, dermoscopy features, biopsy results."
    },
}

# Letter templates
ADVANCED_TEMPLATES = {
    "standard": {"name": "Standard Referral", "tone": "professional"},
    "urgent_referral": {"name": "Urgent Referral", "tone": "urgent", "flag": "URGENT"},
    "comanagement": {"name": "Co-management Letter", "tone": "collaborative"},
    "second_opinion": {"name": "Second Opinion Request", "tone": "consultative"},
    "patient_education": {"name": "Patient Summary", "tone": "simple", "reading_level": "8th grade"},
    "insurance": {"name": "Insurance/Prior Auth", "tone": "formal", "include_codes": True},
}

# Analysis stages (9 stages)
ANALYSIS_STAGES = [
    ("received", "Received", 0),
    ("extracting", "Extracting text", 10),
    ("analyzing_provider", "Identifying provider", 20),
    ("extracting_findings", "Extracting clinical findings", 35),
    ("building_assessment", "Building assessment", 50),
    ("cross_referencing", "Cross-referencing evidence", 65),
    ("structuring", "Structuring output", 80),
    ("validating", "Validating schema", 90),
    ("complete", "Complete", 100),
]

# Letter stages (7 stages)
LETTER_STAGES = [
    ("received", "Received", 0),
    ("loading_context", "Loading context", 15),
    ("selecting_template", "Selecting template", 30),
    ("drafting", "Drafting letter", 50),
    ("formatting", "Formatting", 70),
    ("finalizing", "Finalizing", 90),
    ("complete", "Complete", 100),
]

# In-memory job store (for single-worker; use Redis for multi-worker)
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

# =============================================================================
# SCHEMA VALIDATION
# =============================================================================

REQUIRED_ANALYSIS_FIELDS = [
    "provider_name", "patient_block", "summary_html", "diagnoses", "plan", "references"
]

def validate_analysis(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate analysis output against expected schema"""
    errors = []
    
    for field in REQUIRED_ANALYSIS_FIELDS:
        if field not in data:
            errors.append(f"Missing required field: {field}")
        elif field in ["diagnoses", "plan", "references"]:
            if not isinstance(data[field], list):
                errors.append(f"Field {field} must be a list")
    
    # Validate diagnoses structure
    if "diagnoses" in data and isinstance(data["diagnoses"], list):
        for i, dx in enumerate(data["diagnoses"]):
            if not isinstance(dx, dict):
                errors.append(f"diagnoses[{i}] must be an object")
            elif "title" not in dx:
                errors.append(f"diagnoses[{i}] missing 'title'")
    
    # Validate plan structure
    if "plan" in data and isinstance(data["plan"], list):
        for i, item in enumerate(data["plan"]):
            if not isinstance(item, dict):
                errors.append(f"plan[{i}] must be an object")
            elif "title" not in item:
                errors.append(f"plan[{i}] missing 'title'")
    
    return len(errors) == 0, errors

def coerce_analysis_types(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce analysis data to expected types"""
    result = dict(data)
    
    # Ensure lists
    for field in ["diagnoses", "plan", "references", "warnings", "icd10_codes"]:
        if field not in result:
            result[field] = []
        elif not isinstance(result[field], list):
            result[field] = [result[field]] if result[field] else []
    
    # Ensure strings
    for field in ["provider_name", "patient_block", "patient_name", "summary_html", "patient_summary"]:
        if field not in result:
            result[field] = ""
        elif not isinstance(result[field], str):
            result[field] = str(result[field])
    
    return result

# =============================================================================
# PDF EXTRACTION
# =============================================================================

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    import fitz  # PyMuPDF
    from PIL import Image
    import pytesseract
except ImportError:
    fitz = None
    Image = None
    pytesseract = None

def extract_pdf_text(file_data: bytes) -> str:
    """Extract text from PDF"""
    if PyPDF2 is None:
        return ""
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file_data))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts).strip()
    except Exception:
        return ""

def text_is_meaningful(text: str) -> bool:
    """Check if text is meaningful"""
    s = (text or "").strip()
    if len(s) < 250:
        return False
    alpha = sum(1 for ch in s if ch.isalpha())
    return (alpha / max(len(s), 1)) >= 0.25

def ocr_pdf(pdf_bytes: bytes, max_pages: int = 12) -> Tuple[str, str]:
    """OCR a PDF, returns (text, error)"""
    if not all([fitz, Image, pytesseract]):
        return "", "OCR dependencies not available"
    
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts = []
        for i in range(min(len(doc), max_pages)):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=220, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
            txt = pytesseract.image_to_string(img, config="--psm 6") or ""
            parts.append(txt)
        doc.close()
        return "\n".join(parts).strip(), ""
    except Exception as e:
        return "", str(e)

def extract_text_from_upload(file_storage, force_ocr: bool = False) -> Tuple[str, bool, bool, str]:
    """
    Extract text from uploaded file
    Returns: (text, used_ocr, needs_ocr, error)
    """
    filename = (getattr(file_storage, "filename", "") or "").lower()
    
    try:
        data = file_storage.read()
        file_storage.stream.seek(0)
    except Exception as e:
        return "", False, False, f"Unable to read file: {e}"
    
    if filename.endswith(".pdf"):
        extracted = extract_pdf_text(data)
        
        if text_is_meaningful(extracted) and not force_ocr:
            return extracted, False, False, ""
        
        if not force_ocr:
            return "", False, True, "No readable text"
        
        ocr_text, ocr_err = ocr_pdf(data)
        if ocr_err:
            return "", False, False, ocr_err
        
        if text_is_meaningful(ocr_text):
            return ocr_text, True, False, ""
        
        return extracted or ocr_text, True, False, ""
    
    elif filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
        if not force_ocr:
            return "", False, True, "Image requires OCR"
        
        if not all([Image, pytesseract]):
            return "", False, True, "OCR not available"
        
        try:
            img = Image.open(io.BytesIO(data))
            text = pytesseract.image_to_string(img) or ""
            return text.strip(), True, False, ""
        except Exception as e:
            return "", False, True, str(e)
    
    return "", False, False, "Unsupported file type"

# =============================================================================
# OPENAI INTEGRATION
# =============================================================================

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def get_openai_client():
    """Get OpenAI client"""
    if OpenAI is None:
        return None
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAI(api_key=api_key, timeout=90)

def model_name() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4.1").strip()

def safe_json_loads(s: str) -> Tuple[Optional[Dict], str]:
    """Parse JSON from model output"""
    if not s:
        return None, "Empty output"
    
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj, ""
    except Exception:
        pass
    
    # Try to extract JSON from markdown
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj, ""
        except Exception:
            pass
    
    return None, "Invalid JSON"

def llm_json(prompt: str, temperature: float = 0.2) -> Tuple[Optional[Dict], str]:
    """Call LLM and get JSON response"""
    client = get_openai_client()
    if client is None:
        return None, "OpenAI not configured"
    
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
        return safe_json_loads(text)
    except Exception as e:
        return None, f"LLM error: {e}"

# =============================================================================
# ANALYSIS PROMPT
# =============================================================================

def build_analysis_prompt(note_text: str, specialty: str = "auto") -> str:
    """Build the analysis prompt with specialty handling"""
    specialty_config = SPECIALTIES.get(specialty, SPECIALTIES["auto"])
    specialty_modifier = specialty_config.get("prompt_modifier", "")
    
    prompt = f"""You are a clinical documentation assistant. Analyze this clinical note and extract structured data.

{specialty_modifier}

Return a JSON object with these fields:
- provider_name: string (doctor/provider name)
- patient_block: string (patient demographics as HTML with <br> tags)
- patient_name: string (patient's name)
- summary_html: string (clinical summary as HTML)
- patient_summary: string (plain English summary for patient)
- diagnoses: array of objects with: title, icd10, confidence, ref_nums, bullets
- plan: array of objects with: title, ref_nums, bullets
- references: array of objects with: number, citation, pmid, url, source
- warnings: array of strings (clinical warnings/red flags)
- icd10_codes: array of objects with: code, description, primary (boolean)
- quality_score: object with: score (0-100), quality (poor/fair/good/excellent), issues, suggestions

Clinical Note:
{note_text[:12000]}

Return valid JSON only."""
    
    return prompt

# =============================================================================
# JOB MANAGEMENT
# =============================================================================

def create_job(job_type: str = "analysis", specialty: str = "auto", template: str = "standard") -> str:
    """Create a new job"""
    job_id = uuid.uuid4().hex
    
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "waiting",
            "stage": "received",
            "stage_label": "Received",
            "progress": 0,
            "data": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_type": job_type,
            "specialty": specialty,
            "template": template,
        }
    
    # Also create DB record if user is logged in
    if current_user.is_authenticated:
        try:
            job = Job(
                job_id=job_id,
                job_type=job_type,
                status=JobStatus.WAITING.value,
                specialty=specialty,
                template=template,
                user_id=current_user.id,
                organization_id=current_user.organization_id
            )
            db.session.add(job)
            db.session.commit()
        except Exception:
            pass
    
    return job_id

def update_job_stage(job_id: str, stage: str, label: str, progress: int):
    """Update job stage"""
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["stage"] = stage
            JOBS[job_id]["stage_label"] = label
            JOBS[job_id]["progress"] = progress
    
    # Update DB
    try:
        job = Job.query.filter_by(job_id=job_id).first()
        if job:
            job.set_stage(stage, label, progress)
    except Exception:
        pass

def complete_job(job_id: str, data: Dict[str, Any]):
    """Complete a job"""
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "complete"
            JOBS[job_id]["stage"] = "complete"
            JOBS[job_id]["stage_label"] = "Complete"
            JOBS[job_id]["progress"] = 100
            JOBS[job_id]["data"] = data
    
    try:
        job = Job.query.filter_by(job_id=job_id).first()
        if job:
            job.complete(data)
    except Exception:
        pass

def fail_job(job_id: str, error: str):
    """Fail a job"""
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = error
    
    try:
        job = Job.query.filter_by(job_id=job_id).first()
        if job:
            job.fail(error)
    except Exception:
        pass

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get job status"""
    with JOBS_LOCK:
        return JOBS.get(job_id)

# =============================================================================
# ANALYSIS WORKER
# =============================================================================

def run_analysis(job_id: str, note_text: str, specialty: str = "auto"):
    """Run analysis in background"""
    try:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["status"] = "processing"
        
        # Stage 1: Extracting
        update_job_stage(job_id, "extracting", "Extracting text...", 10)
        
        # Stage 2: Analyzing provider
        update_job_stage(job_id, "analyzing_provider", "Identifying provider...", 20)
        
        # Stage 3: Extracting findings
        update_job_stage(job_id, "extracting_findings", "Extracting clinical findings...", 35)
        
        # Stage 4: Building assessment
        update_job_stage(job_id, "building_assessment", "Building assessment...", 50)
        
        # Build and run prompt
        prompt = build_analysis_prompt(note_text, specialty)
        
        # Stage 5: Cross-referencing
        update_job_stage(job_id, "cross_referencing", "Cross-referencing evidence...", 65)
        
        result, error = llm_json(prompt)
        
        if error:
            fail_job(job_id, error)
            return
        
        if not result:
            fail_job(job_id, "No analysis result")
            return
        
        # Stage 6: Structuring
        update_job_stage(job_id, "structuring", "Structuring output...", 80)
        
        # Coerce types
        result = coerce_analysis_types(result)
        
        # Stage 7: Validating
        update_job_stage(job_id, "validating", "Validating schema...", 90)
        
        # Validate schema
        is_valid, errors = validate_analysis(result)
        result["metadata"] = {
            "analysis_version": "2.0",
            "schema_valid": is_valid,
            "validation_errors": errors if not is_valid else [],
            "specialty": specialty,
            "quality_level": result.get("quality_score", {}).get("quality", "unknown")
        }
        
        # Complete
        complete_job(job_id, result)
        
    except Exception as e:
        fail_job(job_id, f"Analysis error: {e}")

# =============================================================================
# API ROUTES
# =============================================================================

@api_bp.route("/csrf_token")
@login_required
def csrf_token():
    """Get CSRF token"""
    return {"csrf_token": generate_csrf()}

@api_bp.route("/analyze_start", methods=["POST"])
@login_required
@usage_limit_check
def analyze_start():
    """Start analysis job"""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"})
    
    file = request.files["file"]
    if not file.filename:
        return jsonify({"ok": False, "error": "Empty filename"})
    
    handwritten = request.form.get("handwritten") == "1"
    specialty = request.form.get("specialty", "auto")
    
    # Validate specialty
    if specialty not in SPECIALTIES:
        specialty = "auto"
    
    # Extract text
    text, used_ocr, needs_ocr, error = extract_text_from_upload(file, force_ocr=handwritten)
    
    if needs_ocr:
        return jsonify({"ok": False, "needs_ocr": True, "error": error or "OCR required"})
    
    if error and not text:
        return jsonify({"ok": False, "error": error})
    
    if not text or len(text.strip()) < 100:
        return jsonify({"ok": False, "error": "Not enough text extracted"})
    
    # Create job
    job_id = create_job(job_type="analysis", specialty=specialty)
    
    # Log audit
    log_audit("analysis_started", {"job_id": job_id, "specialty": specialty, "used_ocr": used_ocr})
    
    # Track usage
    if current_user.organization:
        current_user.organization.increment_usage()
    
    # Start analysis in background
    thread = threading.Thread(target=run_analysis, args=(job_id, text, specialty))
    thread.start()
    
    return jsonify({"ok": True, "job_id": job_id})

@api_bp.route("/analyze_status")
@login_required
def analyze_status():
    """Get analysis job status"""
    job_id = request.args.get("job_id", "")
    if not job_id:
        return jsonify({"ok": False, "error": "Missing job_id"})
    
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"})
    
    response = {
        "ok": True,
        "status": job["status"],
        "stage": job.get("stage", ""),
        "stage_label": job.get("stage_label", ""),
        "progress": job.get("progress", 0),
    }
    
    if job["status"] == "complete":
        response["data"] = job["data"]
    elif job["status"] == "error":
        response["error"] = job.get("error", "Unknown error")
    
    return jsonify(response)

@api_bp.route("/letter_start", methods=["POST"])
@login_required
def letter_start():
    """Start letter generation"""
    data = request.get_json() or {}
    
    analysis = data.get("analysis")
    if not analysis:
        return jsonify({"ok": False, "error": "Missing analysis data"})
    
    template = data.get("template", "standard")
    if template not in ADVANCED_TEMPLATES:
        template = "standard"
    
    job_id = create_job(job_type="letter", template=template)
    
    # Start letter generation in background
    thread = threading.Thread(target=run_letter_generation, args=(job_id, data, template))
    thread.start()
    
    return jsonify({"ok": True, "job_id": job_id})

def run_letter_generation(job_id: str, form_data: Dict[str, Any], template: str = "standard"):
    """Generate letter in background"""
    try:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["status"] = "processing"
        
        update_job_stage(job_id, "loading_context", "Loading context...", 15)
        update_job_stage(job_id, "selecting_template", "Selecting template...", 30)
        update_job_stage(job_id, "drafting", "Drafting letter...", 50)
        
        template_config = ADVANCED_TEMPLATES.get(template, ADVANCED_TEMPLATES["standard"])
        
        prompt = f"""Generate a professional clinical referral letter.

Template: {template_config['name']}
Tone: {template_config.get('tone', 'professional')}

Form data:
- To: {form_data.get('to_whom', '')}
- From: {form_data.get('from_doctor', '')}
- Recipient type: {form_data.get('recipient_type', 'Physician')}
- Reason: {form_data.get('reason_for_referral', '')}

Analysis: {json.dumps(form_data.get('analysis', {}), indent=2)[:4000]}

Return JSON with:
- letter_plain: plain text letter
- letter_html: HTML formatted letter

Return valid JSON only."""
        
        update_job_stage(job_id, "formatting", "Formatting...", 70)
        
        result, error = llm_json(prompt)
        
        if error:
            fail_job(job_id, error)
            return
        
        update_job_stage(job_id, "finalizing", "Finalizing...", 90)
        
        complete_job(job_id, result or {"letter_plain": "", "letter_html": ""})
        
    except Exception as e:
        fail_job(job_id, str(e))

@api_bp.route("/letter_status")
@login_required
def letter_status():
    """Get letter job status"""
    job_id = request.args.get("job_id", "")
    if not job_id:
        return jsonify({"ok": False, "error": "Missing job_id"})
    
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job not found"})
    
    response = {
        "ok": True,
        "status": job["status"],
        "stage": job.get("stage", ""),
        "stage_label": job.get("stage_label", ""),
        "progress": job.get("progress", 0),
    }
    
    if job["status"] == "complete":
        response["data"] = job["data"]
    elif job["status"] == "error":
        response["error"] = job.get("error", "Unknown error")
    
    return jsonify(response)

@api_bp.route("/specialties")
@login_required
def get_specialties():
    """Get available specialties"""
    return jsonify({
        "ok": True,
        "specialties": [
            {"id": k, "name": v["name"]} for k, v in SPECIALTIES.items()
        ]
    })

@api_bp.route("/templates")
@login_required
def get_templates():
    """Get available letter templates"""
    return jsonify({
        "ok": True,
        "templates": [
            {"id": k, "name": v["name"]} for k, v in ADVANCED_TEMPLATES.items()
        ]
    })

@api_bp.route("/stages")
@login_required
def get_stages():
    """Get analysis/letter stage definitions"""
    return jsonify({
        "ok": True,
        "analysis_stages": [{"id": s[0], "label": s[1], "progress": s[2]} for s in ANALYSIS_STAGES],
        "letter_stages": [{"id": s[0], "label": s[1], "progress": s[2]} for s in LETTER_STAGES],
    })
