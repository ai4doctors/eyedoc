from __future__ import annotations
import re
from typing import Dict

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def extract_demographics(text: str) -> Dict[str, str]:
    t = text or ""

    name = ""
    m = re.search(r"(Patient\s*[:\-]\s*)([A-Z][A-Za-z' ]{2,})", t)
    if m:
        name = _clean(m.group(2))
    else:
        m = re.search(r"(Name\s*[:\-]\s*)([A-Z][A-Za-z'\-, ]{2,})", t)
        if m:
            name = _clean(m.group(2).replace(",", " "))

    dob = ""
    m = re.search(r"(DOB|Date\s*of\s*Birth)\s*[:\-]?\s*([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})", t, re.IGNORECASE)
    if m:
        dob = _clean(m.group(2)).replace("-", "/")

    phn = ""
    m = re.search(r"(PHN|Health\s*(Number|No\.?))\s*[:\-]?\s*([0-9]{6,12})", t, re.IGNORECASE)
    if m:
        phn = _clean(m.group(3))

    phone = ""
    m = re.search(r"(\+?1\s*)?\(?\d{3}\)?\s*[\-\.]?\s*\d{3}\s*[\-\.]?\s*\d{4}", t)
    if m:
        phone = _clean(m.group(0))

    address = ""
    m = re.search(r"(Address)\s*[:\-]?\s*(.+)", t, re.IGNORECASE)
    if m:
        address = _clean(m.group(2))[:160]

    appt = ""
    m = re.search(r"(Visit\s*Date|Date\s*of\s*Visit|Encounter\s*Date)\s*[:\-]?\s*([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})", t, re.IGNORECASE)
    if m:
        appt = _clean(m.group(2)).replace("-", "/")
    else:
        m = re.search(r"([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})", t)
        if m:
            appt = _clean(m.group(1)).replace("-", "/")

    return {
        "patient_name": name,
        "dob": dob,
        "phn": phn,
        "phone": phone,
        "address": address,
        "appointment_date": appt
    }
