import os
from typing import Dict, Any, Optional, List

from .index import open_db, search, DB_DEFAULT


def detect_specialty(note_text: str, diagnoses: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    txt = (note_text or "").lower()
    dx_text = " ".join([(d.get("label") or "").lower() for d in (diagnoses or [])])
    blob = (txt + " " + dx_text).strip()

    dry_eye_terms = [
        "dry eye",
        "ded",
        "mgd",
        "meibomian",
        "blepharitis",
        "collarettes",
        "tear breakup",
        "tbut",
        "osmolarity",
        "mmp",
        "lissamine",
        "fluorescein",
        "schirmer",
        "demodex",
        "ipl",
        "lipiflow",
        "expression",
        "meibography",
        "punctal",
        "punctum",
        "cyclosporine",
        "lifitegrast",
    ]

    if any(t in blob for t in dry_eye_terms):
        return "dry_eye"

    return None


def build_query(specialty: str, note_text: str, analysis: Dict[str, Any]) -> str:
    dx = " ".join([(d.get("label") or "") for d in (analysis.get("diagnoses") or [])])
    base = (note_text or "").strip()
    base = base[:2000]

    if specialty == "dry_eye":
        return (
            "Dry eye disease evidence based diagnosis and stepwise management. "
            "Include symptom assessment, risk factors, tests, staging, and treatment escalation. "
            f"Diagnoses: {dx}. "
            f"Encounter: {base}"
        )

    return f"Evidence based best practices. Diagnoses: {dx}. Encounter: {base}"


def enhance_case(
    llm_json,
    note_text: str,
    analysis: Dict[str, Any],
    specialty: Optional[str] = None,
    db_path: Optional[str] = None,
    top_k: int = 6,
) -> Optional[Dict[str, Any]]:
    """Returns a dict with missing items and suggested plan additions, citing retrieved passages.

    llm_json: callable(prompt: str, schema: dict) -> dict
    """

    if not note_text or not isinstance(analysis, dict):
        return None

    specialty = specialty or detect_specialty(note_text, analysis.get("diagnoses") or [])
    if not specialty:
        return None

    db_path = db_path or os.getenv("GUIDELINE_DB_PATH", "").strip() or DB_DEFAULT
    if not os.path.exists(db_path):
        return None

    q = build_query(specialty, note_text, analysis)

    with open_db(db_path) as conn:
        passages = search(conn, specialty=specialty, query=q, top_k=top_k)

    if not passages:
        return None

    evidence_blocks = []
    for p in passages:
        evidence_blocks.append(
            {
                "source": p.source,
                "pages": f"{p.page_start}-{p.page_end}",
                "text": (p.text or "")[:1200],
            }
        )

    schema = {
        "type": "object",
        "properties": {
            "specialty": {"type": "string"},
            "missed_history": {"type": "array", "items": {"type": "string"}},
            "missed_exam_tests": {"type": "array", "items": {"type": "string"}},
            "missed_differentials": {"type": "array", "items": {"type": "string"}},
            "suggested_plan_additions": {"type": "array", "items": {"type": "string"}},
            "red_flags": {"type": "array", "items": {"type": "string"}},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "source": {"type": "string"},
                        "pages": {"type": "string"},
                    },
                    "required": ["claim", "source", "pages"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": [
            "specialty",
            "missed_history",
            "missed_exam_tests",
            "missed_differentials",
            "suggested_plan_additions",
            "red_flags",
            "citations",
            "notes",
        ],
    }

    prompt = f"""
You are an expert clinician.

Task

1. Read the encounter notes and the current extracted analysis.
2. Use ONLY the provided guideline evidence passages to propose what might be missing and what evidence based treatment steps could be added.
3. Separate what is documented (already in the note) from what is missing or suggested.
4. Do not invent facts about the patient. Suggest items as checks or options.
5. Every non obvious suggestion must be supported by a citation pointing to one of the provided passages.

Encounter notes

{note_text}

Current extracted analysis (JSON)

{analysis}

Guideline evidence passages (JSON list)

{evidence_blocks}

Output

Return JSON matching the given schema.
""".strip()

    out = llm_json(prompt, schema)
    if not isinstance(out, dict):
        return None

    out["specialty"] = specialty
    return out
