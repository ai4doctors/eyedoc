from __future__ import annotations
from typing import List, Dict, Any
import re
import requests

class PubMedClient:
    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, email: str, tool: str, api_key: str = ""):
        self.email = email
        self.tool = tool
        self.api_key = api_key

    def _common_params(self) -> Dict[str, str]:
        p = {"tool": self.tool, "email": self.email, "retmode": "json"}
        if self.api_key:
            p["api_key"] = self.api_key
        return p

    def build_fallback_queries(self, encounter: Dict[str, Any], analysis: Dict[str, Any]) -> List[str]:
        dx = analysis.get("differential", [])
        terms = []
        for item in dx[:3]:
            if isinstance(item, dict):
                terms.append(item.get("diagnosis", ""))
            elif isinstance(item, str):
                terms.append(item)
        text = (encounter.get("clinical_focus") or "") + " " + (analysis.get("summary") or "")
        text = re.sub(r"[^A-Za-z0-9 ,;/]", " ", text)
        terms = [t.strip() for t in terms if t.strip()]
        if not terms and text.strip():
            terms = ["ocular", "ophthalmology"]
        queries = []
        for t in terms[:3]:
            queries.append(f"{t} diagnosis management review")
            queries.append(f"{t} treatment randomized trial")
            queries.append(f"{t}[MeSH Terms] AND treatment")
        return queries[:6]

    def search_with_retries(self, queries: List[str], min_results: int = 3) -> List[Dict[str, Any]]:
        seen_pmids = set()
        results: List[Dict[str, Any]] = []

        candidates = []
        for q in queries or []:
            q = (q or "").strip()
            if q:
                candidates.append(q)

        if not candidates:
            return []

        broadened = []
        for q in candidates:
            broadened.append(q)
            broadened.append(re.sub(r"\"([^\"]+)\"", r"\1", q))
            broadened.append(q + " AND (review[Publication Type] OR guideline[Publication Type])")
            broadened.append(q + " AND humans[MeSH Terms]")
        candidates = self._dedupe(candidates + broadened)

        for q in candidates:
            pmids = self._esearch(q, retmax=10)
            if not pmids:
                continue
            summaries = self._esummary(pmids)
            for s in summaries:
                pmid = s.get("pmid")
                if pmid and pmid not in seen_pmids:
                    seen_pmids.add(pmid)
                    results.append(s)
            if len(results) >= min_results:
                break

        return results[:12]

    def _esearch(self, term: str, retmax: int = 10) -> List[str]:
        url = f"{self.BASE}/esearch.fcgi"
        params = {**self._common_params(), "db": "pubmed", "term": term, "retmax": str(retmax), "sort": "relevance"}
        try:
            r = requests.get(url, params=params, timeout=12)
            if r.status_code != 200:
                return []
            data = r.json()
            return data.get("esearchresult", {}).get("idlist", []) or []
        except Exception:
            return []

    def _esummary(self, pmids: List[str]) -> List[Dict[str, Any]]:
        if not pmids:
            return []
        url = f"{self.BASE}/esummary.fcgi"
        params = {**self._common_params(), "db": "pubmed", "id": ",".join(pmids)}
        try:
            r = requests.get(url, params=params, timeout=12)
            if r.status_code != 200:
                return []
            data = r.json().get("result", {})
            out = []
            for pmid in pmids:
                item = data.get(pmid)
                if not item:
                    continue
                out.append({
                    "pmid": pmid,
                    "title": item.get("title", ""),
                    "authors": ", ".join([a.get("name","") for a in item.get("authors", [])][:6]).strip(", "),
                    "source": item.get("source", ""),
                    "pubdate": item.get("pubdate", ""),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                })
            return out
        except Exception:
            return []

    def _dedupe(self, items: List[str]) -> List[str]:
        seen = set()
        out = []
        for i in items:
            k = (i or "").strip()
            if not k:
                continue
            kl = k.lower()
            if kl in seen:
                continue
            seen.add(kl)
            out.append(k)
        return out
