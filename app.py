
import os
import json
from flask import Flask, render_template, request, jsonify
import PyPDF2

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static"
)

def extract_pdf_text(file_storage) -> str:
    reader = PyPDF2.PdfReader(file_storage)
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()

def get_client():
    if OpenAI is None:
        return None
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    return OpenAI(api_key=key)

def model_name() -> str:
    name = os.getenv("OPENAI_MODEL", "").strip()
    if name:
        return name
    return "gpt4o"

def llm_json(prompt: str) -> dict:
    client = get_client()
    if client is None:
        return {
            "error": "Missing OpenAI client or OPENAI_API_KEY",
            "diagnosis": "",
            "treatment": "",
            "pubmed": [],
            "patient": {"name": "", "dob": "", "phn": ""},
            "letter_plain": ""
        }

    res = client.responses.create(
        model=model_name(),
        input=[
            {"role": "system", "content": "Return valid json only. No markdown. No extra text."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2
    )

    text = res.output_text or ""
    try:
        return json.loads(text)
    except Exception:
        return {"error": "Model did not return valid json", "raw": text}

def build_analyze_prompt(note_text: str) -> str:
    return f"""
You are an expert clinician assistant. You will read an encounter note text extracted from a pdf.
Produce a clinical synthesis that is accurate, conservative, and clearly written.

Return json with this exact schema:
patient: {{ name: string, dob: string, phn: string }}
diagnosis: string
treatment: string
pubmed: array of 3 to 8 items, each item has title string, journal string, year string, pmid string
key_findings: array of short strings
warnings: array of short strings
raw_excerpt: string

Rules:
Use only details supported by the note text. If missing, leave fields blank.
Do not invent demographics.
Diagnosis should be a brief problem list style paragraph.
Treatment should be specific, actionable, and aligned with common standards.
Pubmed items can be reasonable suggestions but must be real looking and relevant. If unsure, leave pmid blank.
raw_excerpt should be the first 1200 characters of the note.

Encounter note text:
{note_text}
""".strip()

def build_letter_prompt(note_text: str, form: dict, analysis: dict) -> str:
    return f"""
You are an expert clinician assistant. Create a concise professional letter using the encounter note and the analysis.

Return json with this exact schema:
letter_plain: string

Write the letter in plain text with clear headings.

Inputs:
form json:
{json.dumps(form, ensure_ascii=False)}

analysis json:
{json.dumps(analysis, ensure_ascii=False)}

Encounter note text:
{note_text}
""".strip()

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    file = request.files.get("pdf")
    if not file:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    text = extract_pdf_text(file)
    prompt = build_analyze_prompt(text)
    data = llm_json(prompt)

    ok = "error" not in data
    if not ok:
        return jsonify({"ok": False, "error": data.get("error", "Analyze failed"), "data": data}), 200

    return jsonify({"ok": True, "data": data}), 200

@app.route("/generate_letter", methods=["POST"])
def generate_letter():
    file = request.files.get("pdf")
    payload_text = request.form.get("payload", "{}")
    try:
        payload = json.loads(payload_text)
    except Exception:
        payload = {}

    form = payload.get("form", {})
    analysis = payload.get("analysis", {})

    note_text = ""
    if file:
        note_text = extract_pdf_text(file)

    prompt = build_letter_prompt(note_text, form, analysis)
    data = llm_json(prompt)

    if "letter_plain" not in data:
        return jsonify({"ok": False, "error": data.get("error", "Letter generation failed"), "data": data}), 200

    return jsonify({"ok": True, "letter_plain": data["letter_plain"]}), 200

@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok", 200

if __name__ == "__main__":
    app.run(debug=True)
