
from flask import Flask, render_template, request, jsonify
import PyPDF2

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static"
)

def extract_pdf_text(file_storage) -> str:
    reader = PyPDF2.PdfReader(file_storage)
    text_parts = []
    for page in reader.pages:
        text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts).strip()

def simple_synthesis(text: str) -> dict:
    short = text[:1200]
    return {
        "patient": {"name": "", "dob": "", "phn": ""},
        "diagnosis": "Draft diagnosis will appear here after analysis.",
        "treatment": "Draft treatment plan will appear here after analysis.",
        "pubmed": [{"title": "PubMed citations will appear here", "pmid": "", "year": "", "journal": ""}],
        "raw_excerpt": short
    }

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    file = request.files.get("pdf")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400
    text = extract_pdf_text(file)
    data = simple_synthesis(text)
    return jsonify({"ok": True, "data": data})

@app.route("/generate_letter", methods=["POST"])
def generate_letter():
    payload = request.get_json(silent=True) or {}
    form = payload.get("form", {})
    analysis = payload.get("analysis", {})

    referring = (form.get("referring_doctor") or "").strip() or "Referring clinician"
    recipient = (form.get("recipient_name") or "").strip() or "Recipient clinician"
    reason = (form.get("reason_for_referral") or "").strip() or "Clinical referral"
    letter_type = (form.get("letter_type") or "").strip() or "Referral letter"
    special = (form.get("special_requests") or "").strip()
    context = (form.get("additional_context") or "").strip()

    diagnosis = analysis.get("diagnosis") or ""
    treatment = analysis.get("treatment") or ""

    lines = []
    lines.append(f"{letter_type}")
    lines.append("")
    lines.append(f"From: {referring}")
    lines.append(f"To: {recipient}")
    lines.append("")
    lines.append(f"Reason for referral: {reason}")
    if context:
        lines.append("")
        lines.append("Additional context:")
        lines.append(context)
    if special:
        lines.append("")
        lines.append("Special requests:")
        lines.append(special)
    lines.append("")
    lines.append("Assessment:")
    lines.append(diagnosis or "Assessment will appear here.")
    lines.append("")
    lines.append("Plan:")
    lines.append(treatment or "Plan will appear here.")
    letter = "\n".join(lines).strip()

    return jsonify({"ok": True, "letter_plain": letter})

@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok", 200

if __name__ == "__main__":
    app.run(debug=True)
