
import os
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Flask, render_template, request, jsonify
import PyPDF2

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

APP_VERSION = os.getenv("APP_VERSION", "dev")

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
    parts = []
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

ANALYZE_SCHEMA = {
    "patient": {"name": "", "dob": "", "phn": ""},
    "diagnosis": "",
    "treatment": "",
    "key_findings": [],
    "warnings": [],
    "pubmed": [],
    "raw_excerpt": ""
}

def analyze_prompt(note_text: str) -> str:
    excerpt = clamp_text(note_text, 12000)
    return f"""
You are a clinician assistant. You are given an encounter note extracted from a PDF.
Produce a conservative clinical synthesis.

You must output VALID JSON only, matching this schema exactly:
patient: {{ name: string, dob: string, phn: string }}
diagnosis: string
treatment: string
key_findings: array of short strings
warnings: array of short strings
pubmed: array of 3 to 10 items, each item:
  title: string
  journal: string
  year: string
  pmid: string
raw_excerpt: string

Rules:
1 Use only facts supported by the note. If unknown, leave empty.
2 Do not invent demographics.
3 Diagnosis should be problem list style, include severity and laterality when present.
4 Treatment should be actionable and stepwise, include follow up triggers.
5 Pubmed: if you are not sure about PMID, leave it empty. Keep citations relevant.
6 raw_excerpt must be the first 1200 characters of the note.

Encounter note:
{excerpt}
""".strip()

def letter_prompt(note_text: str, form: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    excerpt = clamp_text(note_text, 12000)
    return f"""
You are a clinician assistant. Create a clean, professional letter in plain text.

You must output VALID JSON only with this schema:
letter_plain: string

Guidance:
1 Use headings.
2 Stay concise and clinically useful.
3 Do not add facts not present in the note or analysis.
4 If key details are missing, write them as unknown rather than guessing.

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
    return jsonify({"ok": True, "data": data}), 200

@app.post("/generate_letter")
def generate_letter():
    file = request.files.get("pdf")
    payload_text = request.form.get("payload", "{}")
    try:
        payload = json.loads(payload_text)
    except Exception:
        payload = {}

    form = payload.get("form", {}) or {}
    analysis = payload.get("analysis", {}) or {}

    if not file:
        return jsonify({"ok": False, "error": "No PDF uploaded"}), 400

    note_text = extract_pdf_text(file)
    obj, err = llm_json(letter_prompt(note_text, form, analysis))
    if err:
        return jsonify({"ok": False, "error": err}), 200

    letter_plain = (obj or {}).get("letter_plain", "")
    if not isinstance(letter_plain, str) or not letter_plain.strip():
        return jsonify({"ok": False, "error": "Empty letter generated"}), 200

    return jsonify({"ok": True, "letter_plain": letter_plain.strip()}), 200

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

@app.get("/version")
def version():
    return jsonify({"app_version": APP_VERSION, "time_utc": now_utc_iso()}), 200

if __name__ == "__main__":
    app.run(debug=True)
