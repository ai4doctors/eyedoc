import os
import re
import uuid
from urllib.parse import quote
from datetime import date

from flask import Flask, render_template, request, redirect, url_for, session

try:
    import pdfplumber
except Exception:
    pdfplumber = None

APP_NAME = "AI4Health"
TAGLINE = "Evidence ready. Clinician approved."

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev_secret_change_me")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


def extract_text_from_pdf(path: str) -> str:
    if not pdfplumber:
        return ""
    text_chunks = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:20]:
                t = page.extract_text() or ""
                t = t.strip()
                if t:
                    text_chunks.append(t)
    except Exception:
        return ""
    return "\n\n".join(text_chunks)


def normalize_whitespace(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[\t\u00a0]+", " ", s)
    s = re.sub(r" +", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def html_to_plain_text(html: str) -> str:
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</p>", "\n\n", html, flags=re.I)
    html = re.sub(r"</h[1-6]>", "\n\n", html, flags=re.I)
    html = re.sub(r"</li>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    html = html.replace("&nbsp;", " ")
    html = html.replace("&amp;", "&")
    html = html.replace("&lt;", "<")
    html = html.replace("&gt;", ">")
    return normalize_whitespace(html)


def seed_fields(source_text: str) -> dict:
    """Best effort seeding from extracted text.

    This is intentionally conservative to keep stability high.
    """
    s = source_text or ""
    seeded = {
        "patient_name": "",
        "patient_dob": "",
        "referring_name": "",
        "main_complaint": "",
        "assessment": "",
        "plan": "",
    }

    name_m = re.search(r"\b(?:patient|name)\s*[:\-]\s*(.+)", s, flags=re.I)
    if name_m:
        seeded["patient_name"] = normalize_whitespace(name_m.group(1)).split("\n", 1)[0][:80]

    dob_m = re.search(r"\b(?:dob|date of birth)\s*[:\-]\s*([0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{2,4}|[0-9]{4}[/\-][0-9]{1,2}[/\-][0-9]{1,2})", s, flags=re.I)
    if dob_m:
        seeded["patient_dob"] = dob_m.group(1)[:20]

    ref_m = re.search(r"\b(?:to|referring)\s*(?:doctor|clinician)?\s*[:\-]\s*(.+)", s, flags=re.I)
    if ref_m:
        seeded["referring_name"] = normalize_whitespace(ref_m.group(1)).split("\n", 1)[0][:80]

    return seeded


@app.get("/")
def index():
    session.pop("uploaded_filename", None)
    session.pop("source_text", None)
    return render_template("index.html", app_name=APP_NAME, tagline=TAGLINE)


@app.post("/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("index"))

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in {".pdf", ".txt"}:
        return render_template(
            "index.html",
            app_name=APP_NAME,
            tagline=TAGLINE,
            error="Please upload a PDF or a plain text file.",
        )

    safe_id = uuid.uuid4().hex
    stored_name = f"{safe_id}{ext}"
    stored_path = os.path.join(UPLOAD_DIR, stored_name)
    f.save(stored_path)

    if ext == ".txt":
        try:
            with open(stored_path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except Exception:
            text = ""
    else:
        text = extract_text_from_pdf(stored_path)

    session["uploaded_filename"] = stored_name
    session["source_text"] = normalize_whitespace(text)

    return redirect(url_for("step2"))


@app.get("/step2")
def step2():
    source_text = session.get("source_text", "")
    if source_text is None:
        return redirect(url_for("index"))

    seeded = seed_fields(source_text)

    return render_template(
        "step2.html",
        app_name=APP_NAME,
        tagline=TAGLINE,
        extracted_text=source_text,
        seeded=seeded,
    )


@app.post("/generate")
def generate():
    form = request.form

    patient_name = (form.get("patient_name") or "").strip()
    patient_dob = (form.get("patient_dob") or "").strip()
    clinician_name = (form.get("clinician_name") or "").strip()
    clinic_name = (form.get("clinic_name") or "").strip()
    clinic_phone = (form.get("clinic_phone") or "").strip()
    clinic_fax = (form.get("clinic_fax") or "").strip()
    clinic_email = (form.get("clinic_email") or "").strip()
    referring_name = (form.get("referring_name") or "").strip()
    main_complaint = (form.get("main_complaint") or "").strip()
    assessment = (form.get("assessment") or "").strip()
    plan = (form.get("plan") or "").strip()

    letter_html = render_template(
        "letter_body.html",
        app_name=APP_NAME,
        tagline=TAGLINE,
        current_date=date.today().isoformat(),
        patient_name=patient_name,
        patient_dob=patient_dob,
        clinician_name=clinician_name,
        clinic_name=clinic_name,
        clinic_phone=clinic_phone,
        clinic_fax=clinic_fax,
        clinic_email=clinic_email,
        referring_name=referring_name,
        main_complaint=main_complaint,
        assessment=assessment,
        plan=plan,
    )

    subject = f"Clinical letter for {patient_name}".strip() or "Clinical letter"
    email_to = clinic_email
    plain_body = html_to_plain_text(letter_html)

    mailto = ""
    if email_to:
        mailto = f"mailto:{quote(email_to)}?subject={quote(subject)}&body={quote(plain_body)}"

    return render_template(
        "result.html",
        app_name=APP_NAME,
        tagline=TAGLINE,
        letter_html=letter_html,
        mailto=mailto,
        subject=subject,
        plain_body=plain_body,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
