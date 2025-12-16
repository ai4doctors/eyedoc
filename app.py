import os
import uuid
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

from utils.pdf_extract import extract_text_from_pdf
from utils.demographics import extract_demographics
from utils.pubmed import PubMedClient
from utils.llm import LLMClient
from utils.cache import SimpleTTLCache

APP_VERSION = os.getenv("APP_VERSION", "0.2.0")
GIT_SHA = os.getenv("GIT_SHA", os.getenv("RENDER_GIT_COMMIT", "dev"))
BUILD_TIME = os.getenv("BUILD_TIME", "")

UPLOAD_MAX_MB = int(os.getenv("UPLOAD_MAX_MB", "20"))
MAX_CONTENT_LENGTH = UPLOAD_MAX_MB * 1024 * 1024

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

cache = SimpleTTLCache(ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")))

pubmed = PubMedClient(
    email=os.getenv("NCBI_EMAIL", "ai4doctors@integraeyecare.com"),
    tool=os.getenv("NCBI_TOOL", "AI4Doctors"),
    api_key=os.getenv("NCBI_API_KEY", "")
)

llm = LLMClient(
    provider=os.getenv("LLM_PROVIDER", "openai"),
    api_key=os.getenv("OPENAI_API_KEY", ""),
    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

@app.get("/")
def index():
    return render_template(
        "index.html",
        app_version=APP_VERSION,
        git_sha=GIT_SHA,
        build_time=BUILD_TIME
    )

@app.post("/api/analyze")
def api_analyze():
    if "pdf" not in request.files:
        return jsonify({"error": "Missing file field: pdf"}), 400

    f = request.files["pdf"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    filename = secure_filename(f.filename)
    pdf_bytes = f.read()
    if not pdf_bytes:
        return jsonify({"error": "Empty file"}), 400

    clinical_focus = (request.form.get("clinical_focus") or "").strip()
    differential_hint = (request.form.get("differential_hint") or "").strip()

    extracted_text, meta = extract_text_from_pdf(pdf_bytes)
    demographics = extract_demographics(extracted_text)

    encounter = {
        "source_filename": filename,
        "extracted_text": extracted_text,
        "extraction_meta": meta,
        "demographics": demographics,
        "clinical_focus": clinical_focus,
        "differential_hint": differential_hint,
        "created_at": _now_iso(),
    }

    analysis = llm.generate_analysis(encounter)

    pubmed_queries = analysis.get("pubmed_queries") or []
    references = pubmed.search_with_retries(pubmed_queries, min_results=3)

    if not references:
        fallback_queries = pubmed.build_fallback_queries(encounter, analysis)
        references = pubmed.search_with_retries(fallback_queries, min_results=2)

    analysis["references"] = references

    token = str(uuid.uuid4())
    cache.set(token, {"encounter": encounter, "analysis": analysis})

    return jsonify({
        "token": token,
        "encounter": {
            "source_filename": filename,
            "extraction_meta": meta,
            "demographics": demographics,
            "clinical_focus": clinical_focus,
            "differential_hint": differential_hint
        },
        "analysis": analysis,
        "app": {"version": APP_VERSION, "git_sha": GIT_SHA, "build_time": BUILD_TIME}
    })

@app.post("/api/letter")
def api_letter():
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "Missing token"}), 400

    cached = cache.get(token)
    if not cached:
        return jsonify({"error": "Token expired. Please analyze the PDF again."}), 400

    encounter = cached["encounter"]
    analysis = cached["analysis"]

    letter_type = (data.get("letter_type") or "").strip()
    referral_reason = (data.get("referral_reason") or "").strip()

    if not letter_type:
        return jsonify({"error": "Letter type is required"}), 400
    if not referral_reason:
        return jsonify({"error": "Reason for referral is required"}), 400

    referring_doctor = (data.get("referring_doctor") or "").strip()
    refer_to = (data.get("refer_to") or "").strip()
    special_requests = (data.get("special_requests") or "").strip()
    additional_context = (data.get("additional_context") or "").strip()

    demo_override = data.get("demographics") or {}
    demographics = {**(encounter.get("demographics") or {}), **{k: (v or "").strip() for k, v in demo_override.items()}}

    required_demo = ["patient_name", "dob", "phn", "phone", "address", "appointment_date"]
    missing = [k for k in required_demo if not demographics.get(k)]

    clinician_letters = {"referral_oph", "referral_od", "report_gp", "report_insurance"}
    if missing and letter_type in clinician_letters:
        return jsonify({"error": "Missing patient demographics", "missing_fields": missing}), 400

    letter = llm.generate_letter(
        encounter=encounter,
        analysis=analysis,
        letter_type=letter_type,
        referral_reason=referral_reason,
        referring_doctor=referring_doctor,
        refer_to=refer_to,
        special_requests=special_requests,
        additional_context=additional_context,
        demographics=demographics
    )

    return jsonify({
        "html": letter.get("html", ""),
        "plain": letter.get("plain", ""),
        "references": analysis.get("references", []),
        "warnings": letter.get("warnings", [])
    })

@app.get("/health")
def health():
    return jsonify({"ok": True, "version": APP_VERSION, "git_sha": GIT_SHA})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
