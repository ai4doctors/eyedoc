from __future__ import annotations
from typing import Dict, Any, List
import json
import re
import requests
from datetime import datetime

def _s(v: Any) -> str:
    return (v or "").strip()

class LLMClient:
    def __init__(self, provider: str, api_key: str, model: str, timeout_seconds: int = 45):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_seconds

    def enabled(self) -> bool:
        return bool(self.api_key)

    def generate_analysis(self, encounter: Dict[str, Any]) -> Dict[str, Any]:
        if self.enabled():
            try:
                return self._analysis_openai(encounter)
            except Exception:
                pass
        return self._analysis_fallback(encounter)

    def generate_letter(
        self,
        encounter: Dict[str, Any],
        analysis: Dict[str, Any],
        letter_type: str,
        referral_reason: str,
        referring_doctor: str,
        refer_to: str,
        special_requests: str,
        additional_context: str,
        demographics: Dict[str, str]
    ) -> Dict[str, Any]:
        if self.enabled():
            try:
                return self._letter_openai(encounter, analysis, letter_type, referral_reason, referring_doctor, refer_to, special_requests, additional_context, demographics)
            except Exception:
                pass
        return self._letter_fallback(encounter, analysis, letter_type, referral_reason, referring_doctor, refer_to, special_requests, additional_context, demographics)

    def _chat_openai(self, messages: List[Dict[str, str]]) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages, "temperature": 0.2}
        r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        return j["choices"][0]["message"]["content"]

    def _coerce_json(self, content: str) -> Dict[str, Any]:
        try:
            return json.loads(content)
        except Exception:
            pass
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}

    def _analysis_openai(self, encounter: Dict[str, Any]) -> Dict[str, Any]:
        system = (
            "You are an assistant for clinicians. You do not issue orders. "
            "You summarize the encounter, propose a ranked differential diagnosis, "
            "list confirmatory steps, and propose a correlated management plan. "
            "Output must be structured JSON."
        )
        user = {
            "task": "analyze_encounter",
            "clinical_focus": encounter.get("clinical_focus",""),
            "differential_hint": encounter.get("differential_hint",""),
            "extracted_text": (encounter.get("extracted_text","") or "")[:14000],
            "output_schema": {
                "summary": "4-8 lines",
                "differential": [{"diagnosis":"", "probability":"high|moderate|low", "rationale":"1-2 lines"}],
                "next_steps": ["tests, red flags, referrals, imaging etc"],
                "plan": ["treatment items aligned with top diagnoses"],
                "pubmed_queries": ["5-12 PubMed search queries. Use MeSH terms when possible."]
            }
        }
        content = self._chat_openai([{"role":"system","content":system},{"role":"user","content":json.dumps(user)}])
        data = self._coerce_json(content)
        return self._normalize_analysis(data, encounter)

    def _normalize_analysis(self, data: Dict[str, Any], encounter: Dict[str, Any]) -> Dict[str, Any]:
        fb = self._analysis_fallback(encounter)
        return {
            "summary": _s(data.get("summary")) or fb["summary"],
            "differential": data.get("differential") or fb["differential"],
            "next_steps": data.get("next_steps") or fb["next_steps"],
            "plan": data.get("plan") or fb["plan"],
            "pubmed_queries": data.get("pubmed_queries") or fb["pubmed_queries"]
        }

    def _analysis_fallback(self, encounter: Dict[str, Any]) -> Dict[str, Any]:
        blob = " ".join([(encounter.get("clinical_focus") or ""), (encounter.get("differential_hint") or ""), (encounter.get("extracted_text") or "")]).lower()
        ddx = []
        if any(k in blob for k in ["glaucoma", "iop", "rnfl", "visual field", "vft"]):
            ddx.append({"diagnosis":"Glaucoma suspect", "probability":"high", "rationale":"Risk markers noted; confirm with repeat testing and risk assessment."})
        if any(k in blob for k in ["amd", "drusen", "macular"]):
            ddx.append({"diagnosis":"Age-related macular degeneration, early or intermediate", "probability":"moderate", "rationale":"Macular findings may suggest drusen or pigmentary change; correlate with OCT and photos."})
        if any(k in blob for k in ["keratitis", "photophobia", "infiltrate", "ulcer"]):
            ddx.append({"diagnosis":"Keratitis (infectious vs inflammatory)", "probability":"high", "rationale":"Corneal symptoms or findings suggest keratitis; urgent evaluation may be required."})
        if not ddx:
            ddx.append({"diagnosis":"Ocular surface or refractive etiology", "probability":"moderate", "rationale":"Limited extractable detail; correlate symptoms with refraction and ocular surface exam."})

        next_steps = [
            "Confirm key symptoms, onset, laterality, and red flags; document succinctly.",
            "Repeat or complete targeted testing aligned to the top differential (e.g., OCT, VFT, photos, stain, TBUT).",
            "Escalate urgently if pain, photophobia, reduced vision, APD, or corneal infiltrate is present."
        ]
        plan = [
            "Prioritize management aligned to the most likely diagnosis; set follow-up based on risk and symptoms.",
            "Document response targets and return precautions; update plan after results are confirmed.",
            "If referral is indicated, include a clear clinical question and a focused reason for referral."
        ]
        pubmed_queries = []
        for d in ddx[:3]:
            dx = d["diagnosis"]
            pubmed_queries += [f"{dx} management review", f"{dx} guidelines", f"{dx}[MeSH Terms] AND therapy"]
        return {"summary":"Draft generated from extracted encounter text. Please verify clinical details.", "differential":ddx, "next_steps":next_steps, "plan":plan, "pubmed_queries":pubmed_queries[:10]}

    def _letter_openai(
        self,
        encounter: Dict[str, Any],
        analysis: Dict[str, Any],
        letter_type: str,
        referral_reason: str,
        referring_doctor: str,
        refer_to: str,
        special_requests: str,
        additional_context: str,
        demographics: Dict[str, str]
    ) -> Dict[str, Any]:
        system = (
            "You write excellent medical letters with clean formatting and clinical tone. "
            "You MUST weave special requests and additional context seamlessly, rewriting them professionally. "
            "Do not copy those fields verbatim. "
            "Use the encounter details as the primary source and reformat them into a readable narrative. "
            "Return JSON with fields html and plain."
        )
        user = {
            "task": "generate_letter",
            "letter_type": letter_type,
            "referral_reason": referral_reason,
            "referring_doctor": referring_doctor,
            "refer_to": refer_to,
            "special_requests": special_requests,
            "additional_context": additional_context,
            "demographics": demographics,
            "encounter_text": (encounter.get("extracted_text","") or "")[:14000],
            "analysis_summary": analysis.get("summary",""),
            "differential": analysis.get("differential",[]),
            "plan": analysis.get("plan",[]),
            "formatting_requirements": [
                "Top block includes patient name, DOB, PHN, phone, address, appointment date, letter date",
                "Clear headings with whitespace",
                "Professional close and signature"
            ]
        }
        content = self._chat_openai([{"role":"system","content":system},{"role":"user","content":json.dumps(user)}])
        data = self._coerce_json(content)
        html = _s(data.get("html"))
        plain = _s(data.get("plain"))
        if not html and plain:
            html = "<div>" + self._escape_html(plain).replace("\n","<br>") + "</div>"
        return {"html": html, "plain": plain, "warnings": []}

    def _letter_fallback(
        self,
        encounter: Dict[str, Any],
        analysis: Dict[str, Any],
        letter_type: str,
        referral_reason: str,
        referring_doctor: str,
        refer_to: str,
        special_requests: str,
        additional_context: str,
        demographics: Dict[str, str]
    ) -> Dict[str, Any]:
        from_line = self._escape_html(referring_doctor or "Referring clinician")
        to_line = self._escape_html(refer_to or "Colleague")
        demo = demographics or {}
        patient_block = f"""
        <div class="letter-demographics">
          <div><strong>Patient:</strong> {self._escape_html(demo.get("patient_name",""))}</div>
          <div><strong>DOB:</strong> {self._escape_html(demo.get("dob",""))} &nbsp;&nbsp; <strong>PHN:</strong> {self._escape_html(demo.get("phn",""))}</div>
          <div><strong>Phone:</strong> {self._escape_html(demo.get("phone",""))}</div>
          <div><strong>Address:</strong> {self._escape_html(demo.get("address",""))}</div>
          <div><strong>Appointment date:</strong> {self._escape_html(demo.get("appointment_date",""))}</div>
          <div><strong>Letter date:</strong> {datetime.now().strftime("%Y/%m/%d")}</div>
        </div>
        """

        notes = []
        if special_requests:
            notes.append(self._rewrite_intent(special_requests))
        if additional_context:
            notes.append(self._rewrite_intent(additional_context))
        notes_html = ""
        if notes:
            notes_html = "<h3>Additional notes</h3><ul>" + "".join([f"<li>{self._escape_html(n)}</li>" for n in notes]) + "</ul>"

        ddx = analysis.get("differential", [])
        ddx_html = "<ol>" + "".join([f"<li><strong>{self._escape_html(d.get('diagnosis',''))}</strong> <span class='muted'>({self._escape_html(d.get('probability',''))})</span><div class='small'>{self._escape_html(d.get('rationale',''))}</div></li>" for d in ddx]) + "</ol>"
        plan = analysis.get("plan", [])
        plan_html = "<ul>" + "".join([f"<li>{self._escape_html(p)}</li>" for p in plan]) + "</ul>"

        html = f"""
        <div class="letter">
          <div class="letter-header">
            <div><strong>To:</strong> {to_line}</div>
            <div><strong>From:</strong> {from_line}</div>
          </div>
          {patient_block}
          <h3>Reason for referral</h3>
          <p>{self._escape_html(referral_reason)}</p>
          <h3>Clinical summary</h3>
          <p>{self._escape_html(analysis.get("summary",""))}</p>
          <h3>Ranked differential</h3>
          {ddx_html}
          <h3>Plan</h3>
          {plan_html}
          {notes_html}
          <p class="letter-close">Sincerely,<br>{from_line}</p>
        </div>
        """
        plain = self._strip_tags(html)
        return {"html": html, "plain": plain, "warnings": []}

    def _rewrite_intent(self, s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        s = re.sub(r"\bask for\b", "please consider", s, flags=re.IGNORECASE)
        s = re.sub(r"\bask\b", "please address", s, flags=re.IGNORECASE)
        return s[0].upper() + s[1:] if s else s

    def _escape_html(self, s: str) -> str:
        return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    def _strip_tags(self, html: str) -> str:
        txt = re.sub(r"<br\s*/?>", "\n", html)
        txt = re.sub(r"</p>", "\n\n", txt)
        txt = re.sub(r"<[^>]+>", "", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        return txt.strip()
