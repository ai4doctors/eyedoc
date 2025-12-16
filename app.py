import os
import re
import time
import uuid
import html
from datetime import datetime
from typing import Dict, Any, List, Tuple

import requests
from flask import Flask, render_template, request, jsonify
from pypdf import PdfReader

APP_VERSION = os.getenv("APP_VERSION", "0.3.0")
APP_FOOTER_BLURB = "Created by Dr. Henry Reis for clinicians who value clarity, speed, and evidence."

ENCOUNTER_CACHE: Dict[str, Dict[str, Any]] = {}

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

STOPWORDS = {
    "the","and","or","of","to","in","for","with","on","at","by","from","a","an","is","are","was","were","be","been",
    "patient","pt","hx","history","exam","assessment","plan","notes","today","yesterday","clinic"
}

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

def _io_bytes(b: bytes):
    import io
    return io.BytesIO(b)

def normalize_text(s: str) -> str:
    s = s.replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()

def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(_io_bytes(file_bytes))
    chunks = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t:
            chunks.append(t)
    return normalize_text("\n".join(chunks))

def extract_demographics(text: str) -> Dict[str, str]:
    demo = {
        "name":"", "dob":"", "phn":"", "phone":"", "address":"",
        "appt_date":"", "letter_date": datetime.now().strftime("%Y-%m-%d")
    }

    for p in [r"Patient\s*Name\s*[:\-]?\s*(.+)", r"\bName\b\s*[:\-]?\s*(.+)", r"\bPt\b\s*[:\-]?\s*(.+)"]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            cand = m.group(1).split("\n")[0].strip()
            cand = re.sub(r"\s{2,}", " ", cand)
            if 3 <= len(cand) <= 80:
                demo["name"] = cand
                break

    for p in [
        r"DOB\s*[:\-]?\s*([0-9]{4}[\-/][0-9]{2}[\-/][0-9]{2})",
        r"DOB\s*[:\-]?\s*([0-9]{2}[\-/][0-9]{2}[\-/][0-9]{4})",
        r"Date\s*of\s*Birth\s*[:\-]?\s*([0-9]{4}[\-/][0-9]{2}[\-/][0-9]{2})",
        r"Date\s*of\s*Birth\s*[:\-]?\s*([0-9]{2}[\-/][0-9]{2}[\-/][0-9]{4})",
    ]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            demo["dob"] = m.group(1)
            break

    m = re.search(r"\bPHN\b\s*[:\-]?\s*([0-9\s]{10,14})", text, re.IGNORECASE)
    if m:
        phn = re.sub(r"\s+", "", m.group(1))
        if 9 <= len(phn) <= 12:
            demo["phn"] = phn

    m = re.search(r"\b(Phone|Tel|Telephone)\b\s*[:\-]?\s*(\+?1?\s*\(?\d{3}\)?\s*[\-\s]?\d{3}[\-\s]?\d{4})", text, re.IGNORECASE)
    if m:
        demo["phone"] = m.group(2).strip()

    address_candidates = []
    for line in text.split("\n"):
        l = line.strip()
        if len(l) < 8:
            continue
        if re.search(r"\d+\s+[A-Za-z].*(Street|St\b|Avenue|Ave\b|Road|Rd\b|Boulevard|Blvd\b|Drive|Dr\b|Way\b)", l, re.IGNORECASE):
            address_candidates.append(l)
    if address_candidates:
        demo["address"] = address_candidates[0]

    for p in [
        r"(Date\s*of\s*Visit|Visit\s*Date|Exam\s*Date|Appointment\s*Date)\s*[:\-]?\s*([0-9]{4}[\-/][0-9]{2}[\-/][0-9]{2})",
        r"(Date\s*of\s*Visit|Visit\s*Date|Exam\s*Date|Appointment\s*Date)\s*[:\-]?\s*([0-9]{2}[\-/][0-9]{2}[\-/][0-9]{4})",
    ]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            demo["appt_date"] = m.group(2)
            break

    return demo

def extract_exam_findings(text: str) -> Dict[str, str]:
    sections = {
        "chief_complaint": "",
        "history": "",
        "meds_allergies": "",
        "visual_acuity": "",
        "refraction": "",
        "iop": "",
        "slit_lamp": "",
        "fundus": "",
        "imaging": "",
        "assessment": "",
        "plan": "",
    }
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    joined = "\n".join(lines)

    def grab_after(labels: List[str], max_chars: int) -> str:
        for lab in labels:
            m = re.search(rf"(?i)\b{re.escape(lab)}\b\s*[:\-]?\s*(.+)", joined)
            if m:
                return m.group(1).strip()[:max_chars]
        return ""

    sections["chief_complaint"] = grab_after(["Chief Complaint","CC","Reason for Visit","Complaint"], 600)
    sections["history"] = grab_after(["History of Present Illness","HPI","History"], 1200)
    sections["meds_allergies"] = grab_after(["Meds","Medications","Allergies"], 800)
    sections["visual_acuity"] = grab_after(["VA","Visual Acuity"], 700)
    sections["refraction"] = grab_after(["Refraction","Rx"], 900)
    sections["iop"] = grab_after(["IOP","Intraocular Pressure","Tonometry"], 600)
    sections["slit_lamp"] = grab_after(["Slit Lamp","Anterior Segment","SLE"], 1600)
    sections["fundus"] = grab_after(["Fundus","Posterior Segment","DFE"], 1600)
    sections["imaging"] = grab_after(["OCT","Fundus Photo","Imaging"], 1200)
    sections["assessment"] = grab_after(["Assessment","Impression","Dx","Diagnosis"], 1400)
    sections["plan"] = grab_after(["Plan","Treatment","Management"], 1600)

    return sections

def keywords_for_pubmed(text: str, max_terms: int = 10) -> List[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text)
    out, seen = [], set()
    for t in tokens:
        tl = t.lower()
        if tl in STOPWORDS:
            continue
        if tl in seen:
            continue
        if len(tl) > 28:
            continue
        seen.add(tl)
        out.append(t)
        if len(out) >= max_terms:
            break
    return out

def pubmed_search(query: str, retmax: int = 8) -> List[Dict[str, str]]:
    params = {"db":"pubmed","term":query,"retmode":"json","retmax":str(retmax),"sort":"relevance"}
    r = requests.get(PUBMED_ESEARCH, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    ids = data.get("esearchresult", {}).get("idlist", []) or []
    if not ids:
        return []
    sparams = {"db":"pubmed","id":",".join(ids),"retmode":"json"}
    r2 = requests.get(PUBMED_ESUMMARY, params=sparams, timeout=12)
    r2.raise_for_status()
    d2 = r2.json()
    result = d2.get("result", {})
    papers = []
    for pid in ids:
        item = result.get(pid)
        if not item:
            continue
        title = (item.get("title","") or "").strip().rstrip(".")
        authors = item.get("authors", [])
        first_author = authors[0].get("name") if authors else ""
        year = ""
        pubdate = item.get("pubdate","") or ""
        m = re.search(r"(19|20)\d{2}", pubdate)
        if m:
            year = m.group(0)
        journal = item.get("fulljournalname","") or item.get("source","") or ""
        link = f"https://pubmed.ncbi.nlm.nih.gov/{pid}/"
        papers.append({"pmid":pid,"title":title,"first_author":first_author,"year":year,"journal":journal,"link":link})
    return papers

def pubmed_with_fallback(text: str) -> Tuple[List[Dict[str,str]], str]:
    terms = keywords_for_pubmed(text, max_terms=10)
    if terms:
        q1 = " AND ".join(terms[:4])
        p = pubmed_search(q1, retmax=8)
        if p:
            return p, q1

        q2 = " OR ".join(terms[:7])
        p = pubmed_search(q2, retmax=8)
        if p:
            return p, q2

    q3 = "ophthalmology clinical management review"
    return pubmed_search(q3, retmax=8), q3

def basic_ranked_ddx(sections: Dict[str,str]) -> List[str]:
    ddx = []
    a = sections.get("assessment","").strip()
    if a:
        ddx.append(a.split("\n")[0].strip()[:160])

    extras = [
        "Dry eye disease with meibomian gland dysfunction",
        "Allergic conjunctivitis",
        "Blepharitis",
        "Refractive error or accommodative strain",
        "Early cataract or lens changes",
        "Retinal pathology requiring exclusion based on symptoms and exam"
    ]
    for e in extras:
        if len(ddx) >= 6:
            break
        if e.lower() not in " ".join(ddx).lower():
            ddx.append(e)
    return ddx

def basic_plan_from_ddx(ddx: List[str]) -> List[str]:
    plan = []
    for d in ddx[:3]:
        dl = d.lower()
        if "dry eye" in dl or "meibom" in dl or "bleph" in dl:
            plan.append("Lid hygiene and warm compresses; consider thermal pulsation or IPL if indicated; preservative free lubricants; consider topical anti inflammatory therapy when appropriate.")
        elif "allergic" in dl:
            plan.append("Topical antihistamine mast cell stabilizer; review triggers; consider monitored short course topical steroid if severe.")
        elif "cataract" in dl:
            plan.append("Document best corrected acuity and functional impact; discuss surgical referral if appropriate; optimize ocular surface if proceeding to biometry.")
        elif "retinal" in dl:
            plan.append("Dilated fundus examination and imaging as needed; urgent retinal referral if flashes, floaters, curtain, or suspicious findings.")
        else:
            plan.append("Targeted testing to confirm or rule out key diagnoses; treat based on confirmed etiology.")
    plan.append("Follow up based on severity and response to treatment, with return precautions.")
    return plan

def weave_requests(reason: str, special: str, context: str) -> str:
    parts = [f"The reason for this referral is {reason.strip().rstrip('.') }."]
    if special.strip():
        parts.append(f"If possible, I would appreciate the following: {special.strip().rstrip('.')}.")
    if context.strip():
        parts.append(f"Additional context that may be helpful: {context.strip().rstrip('.')}.")
    return " ".join(parts)

def html_letter(demo: Dict[str,str], sections: Dict[str,str], letter_type: str, referring: str, refer_to: str, reason: str, special: str, context: str, pubmed: List[Dict[str,str]]) -> str:
    now = demo.get("letter_date") or datetime.now().strftime("%Y-%m-%d")
    appt = demo.get("appt_date") or ""
    header = f"""<div class="letter">
      <div class="letter-top">
        <div><strong>Date:</strong> {html.escape(now)}</div>
        <div class="mt6"><strong>Re:</strong> {html.escape(demo.get("name",""))}</div>
        <div class="meta">
          <div><strong>DOB:</strong> {html.escape(demo.get("dob",""))}</div>
          <div><strong>PHN:</strong> {html.escape(demo.get("phn",""))}</div>
          <div><strong>Phone:</strong> {html.escape(demo.get("phone",""))}</div>
          <div><strong>Address:</strong> {html.escape(demo.get("address",""))}</div>
          <div><strong>Date of visit:</strong> {html.escape(appt)}</div>
        </div>
      </div>
    """

    to_line = ""
    if letter_type in {"referral_ophthalmologist","referral_optometrist","report_gp","report_insurance"}:
        to_line = f"""<div class="mt16"><strong>To:</strong> {html.escape(refer_to)}</div>"""
    from_line = f"""<div class="mt8"><strong>From:</strong> {html.escape(referring)}</div>"""

    req_block = f"""<div class="mt12">{html.escape(weave_requests(reason, special, context))}</div>"""

    def p(label: str, val: str) -> str:
        if not val.strip():
            return ""
        return f"""<div class="mt10"><strong>{html.escape(label)}:</strong><div class="mt4">{html.escape(val)}</div></div>"""

    findings_html = "<div class='mt16'><strong>Encounter details</strong></div>"
    findings_html += p("Chief complaint", sections.get("chief_complaint",""))
    findings_html += p("History", sections.get("history",""))
    findings_html += p("Medications and allergies", sections.get("meds_allergies",""))
    findings_html += p("Visual acuity", sections.get("visual_acuity",""))
    findings_html += p("Refraction", sections.get("refraction",""))
    findings_html += p("IOP", sections.get("iop",""))
    findings_html += p("Slit lamp", sections.get("slit_lamp",""))
    findings_html += p("Fundus", sections.get("fundus",""))
    findings_html += p("Imaging", sections.get("imaging",""))

    assessment_html = ""
    if sections.get("assessment","").strip():
        assessment_html = f"""<div class="mt16"><strong>Assessment</strong></div><div class="mt6">{html.escape(sections["assessment"])}</div>"""

    plan_html = ""
    if sections.get("plan","").strip():
        plan_html = f"""<div class="mt16"><strong>Plan</strong></div><div class="mt6">{html.escape(sections["plan"])}</div>"""

    refs_html = ""
    if pubmed:
        items = []
        for ppr in pubmed:
            t = html.escape(ppr["title"])
            j = html.escape(ppr.get("journal",""))
            y = html.escape(ppr.get("year",""))
            a = html.escape(ppr.get("first_author",""))
            link = html.escape(ppr["link"])
            items.append(f"""<li><a href="{link}" target="_blank" rel="noopener">{t}</a><div class="refmeta">{a} {y} {j}</div></li>""")
        refs_html = "<div class='mt16'><strong>Selected PubMed references</strong></div><ol class='refs mt6'>" + "".join(items) + "</ol>"

    closing = "<div class='mt20'>Sincerely,</div>"
    closing += f"""<div class="mt10"><strong>{html.escape(referring)}</strong></div>"""
    return header + to_line + from_line + req_block + findings_html + assessment_html + plan_html + refs_html + closing + "</div>"

@app.get("/")
def index():
    return render_template("index.html", app_version=APP_VERSION, footer_blurb=APP_FOOTER_BLURB)

@app.post("/api/analyze")
def api_analyze():
    if "pdf" not in request.files:
        return jsonify({"error":"No PDF uploaded"}), 400
    f = request.files["pdf"]
    b = f.read()
    if not b:
        return jsonify({"error":"Empty file"}), 400

    try:
        text = extract_text_from_pdf(b)
    except Exception as e:
        return jsonify({"error": f"PDF text extraction failed: {str(e)}"}), 400

    demo = extract_demographics(text)
    sections = extract_exam_findings(text)

    ddx = basic_ranked_ddx(sections)
    plan = basic_plan_from_ddx(ddx)

    pubmed, query_used = pubmed_with_fallback(" ".join([sections.get("assessment",""), sections.get("plan",""), sections.get("chief_complaint",""), sections.get("history","")]))

    token = str(uuid.uuid4())
    ENCOUNTER_CACHE[token] = {
        "created": time.time(),
        "demo": demo,
        "sections": sections,
        "ddx": ddx,
        "plan": plan,
        "pubmed": pubmed,
        "pubmed_query": query_used,
        "raw_text": text[:150000],
    }

    return jsonify({
        "token": token,
        "demographics": demo,
        "sections": sections,
        "ddx": ddx,
        "plan": plan,
        "pubmed": pubmed,
        "pubmed_query": query_used
    })

@app.post("/api/letter")
def api_letter():
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token or token not in ENCOUNTER_CACHE:
        return jsonify({"error":"Invalid or expired encounter token. Upload the PDF again."}), 400

    letter_type = (data.get("letter_type") or "").strip()
    referring = (data.get("referring_doctor") or "").strip()
    refer_to = (data.get("refer_to") or "").strip()
    reason = (data.get("reason_for_referral") or "").strip()
    special = (data.get("special_requests") or "").strip()
    context = (data.get("additional_context") or "").strip()

    if not letter_type:
        return jsonify({"error":"Letter type is required"}), 400
    if not reason:
        return jsonify({"error":"Reason for referral is required"}), 400
    if not referring:
        return jsonify({"error":"Referring doctor is required"}), 400
    if letter_type in {"referral_ophthalmologist","referral_optometrist","report_gp","report_insurance"} and not refer_to:
        return jsonify({"error":"Refer to is required for this letter type"}), 400

    enc = ENCOUNTER_CACHE[token]
    demo = enc["demo"].copy()

    overrides = data.get("demographics_overrides") or {}
    for k in ["name","dob","phn","phone","address","appt_date","letter_date"]:
        if overrides.get(k) is not None and str(overrides.get(k)).strip():
            demo[k] = str(overrides.get(k)).strip()

    if letter_type != "report_patient":
        missing = [k for k in ["name","dob","phn"] if not (demo.get(k) or "").strip()]
        if missing:
            return jsonify({"error":"Missing required patient demographics", "missing": missing}), 400

    html_out = html_letter(
        demo=demo,
        sections=enc["sections"],
        letter_type=letter_type,
        referring=referring,
        refer_to=refer_to,
        reason=reason,
        special=special,
        context=context,
        pubmed=enc["pubmed"],
    )
    return jsonify({"html": html_out})

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "version": APP_VERSION})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")), debug=True)
