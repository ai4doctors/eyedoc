import os
import json
from typing import Any, Dict, List, Optional, Tuple

from .store import ensure_db, search_chunks


def _enabled() -> bool:
    v = (os.getenv("ENABLE_CANONICAL_ENGINE") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def _specialty_from_analysis(analysis: Dict[str, Any]) -> str:
    # For now, only dry eye is wired as flagship
    dx = analysis.get("diagnoses") or []
    labels = []
    for item in dx:
        if isinstance(item, dict):
            lab = (item.get("label") or "").lower()
            if lab:
                labels.append(lab)
    joined = " ".join(labels)
    if "dry" in joined and "eye" in joined:
        return "dry_eye"
    if "meibom" in joined or "mgd" in joined:
        return "dry_eye"
    return ""


def _load_checklist_schema(specialty: str) -> Optional[Dict[str, Any]]:
    if not specialty:
        return None
    path = os.path.join(os.path.dirname(__file__), "checklists", f"{specialty}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf8") as f:
            return json.load(f)
    except Exception:
        return None


def _make_citation_list(chunks: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for c in chunks:
        out.append({
            "citation_id": c.citation_id,
            "title": c.title,
            "version": c.version,
            "year": c.year,
            "section": c.section,
            "page": c.page,
        })
    return out


def build_guideline_lanes(
    llm_json,
    note_text: str,
    analysis: Dict[str, Any],
    openai_client,
    embed_model: str,
    k: int = 10,
) -> Tuple[Optional[Dict[str, Any]], str]:
    if not _enabled():
        return None, "disabled"

    ensure_db()
    specialty = _specialty_from_analysis(analysis)
    if not specialty:
        return None, "no specialty match"

    schema = _load_checklist_schema(specialty)
    if not schema:
        return None, "missing checklist schema"

    query = f"{specialty} diagnosis and management guideline checklist"
    chunks, err = search_chunks(openai_client, embed_model, specialty, query, k=k)
    if err:
        return None, err

    citations = _make_citation_list(chunks)

    context_snips = []
    for c in chunks:
        # Keep snippets short to avoid dumping large copyrighted text
        txt = (c.preview or c.text or "").strip()
        if len(txt) > 420:
            txt = txt[:420].rstrip() + "..."
        context_snips.append({
            "citation_id": c.citation_id,
            "section": c.section,
            "page": c.page,
            "text": txt,
        })

    prompt = {
        "task": "guideline_lanes",
        "specialty": specialty,
        "checklist_schema": schema,
        "note_text": note_text,
        "analysis": analysis,
        "retrieved_guideline_snippets": context_snips,
        "rules": {
            "documented_only": True,
            "missing_is_questions": True,
            "suggestions_require_citations": True,
            "allowed_citation_ids": [c["citation_id"] for c in context_snips],
        },
        "output_schema": {
            "documented": {
                "checklist": "object matching checklist_schema fields, values only if present, else null",
                "supporting": "list of short bullet sentences quoting nothing, each with citation_ids list",
            },
            "missing_but_important": {
                "items": "list of items with field, why, prompt_to_ask",
            },
            "suggested_plan": {
                "items": "list of items with recommendation, rationale, confidence (high medium low), citation_ids",
            },
            "audit": {
                "used_citation_ids": "list",
                "notes": "short",
            },
        },
        "style": "clinical, conservative, no hallucinated findings",
    }

    obj, perr = llm_json(json.dumps(prompt, ensure_ascii=False), temperature=0.1)
    if perr or not isinstance(obj, dict):
        return None, perr or "model failed"

    lanes = obj

    # Enforce citations rule in code
    plan = ((lanes.get("suggested_plan") or {}).get("items") or [])
    kept = []
    for it in plan:
        if not isinstance(it, dict):
            continue
        cids = it.get("citation_ids")
        if isinstance(cids, list) and cids:
            # keep only allowed ids
            allow = set([c["citation_id"] for c in context_snips])
            clean = [x for x in cids if isinstance(x, str) and x in allow]
            if clean:
                it["citation_ids"] = clean
                kept.append(it)
    if "suggested_plan" not in lanes or not isinstance(lanes.get("suggested_plan"), dict):
        lanes["suggested_plan"] = {}
    lanes["suggested_plan"]["items"] = kept

    # Attach citations metadata for display
    lanes["citations"] = citations

    # Audit trail
    used_ids = []
    for it in kept:
        used_ids.extend(it.get("citation_ids") or [])
    used_ids = sorted(list(set([x for x in used_ids if isinstance(x, str)])))
    lanes["audit"] = lanes.get("audit") or {}
    lanes["audit"]["used_citation_ids"] = used_ids

    return lanes, ""
