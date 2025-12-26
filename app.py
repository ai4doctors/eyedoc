
from flask import Flask, request, render_template, redirect, url_for
import PyPDF2
import re
from datetime import datetime

app = Flask(__name__)

def extract_text(pdf_file):
    reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text

def parse_patient(text):
    patient = {}

    name_match = re.search(r"Patient Encounter\s+([A-Za-z ]+)", text)
    dob_match = re.search(r"DOB:\s*(\d{2}/\d{2}/\d{4})", text)
    sex_match = re.search(r"Sex:\s*(Male|Female)", text)
    date_match = re.search(r"Date:\s*(\d{2}/\d{2}/\d{4})", text)

    patient["name"] = name_match.group(1).strip() if name_match else "Unknown"
    patient["dob"] = dob_match.group(1) if dob_match else "Unknown"
    patient["sex"] = sex_match.group(1) if sex_match else "Unknown"
    patient["encounter_date"] = date_match.group(1) if date_match else "Unknown"

    patient["raw_text"] = text
    return patient

@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        pdf = request.files["pdf"]
        text = extract_text(pdf)
        patient = parse_patient(text)
        request.session = patient
        return render_template("confirm.html", patient=patient)
    return render_template("upload.html")

@app.route("/summary")
def summary():
    p = request.session
    return render_template("summary.html", p=p)

@app.route("/diagnosis")
def diagnosis():
    p = request.session
    return render_template("diagnosis.html", p=p)

@app.route("/referral")
def referral():
    p = request.session
    return render_template("referral.html", p=p)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
