import argparse
import os
import json
import uuid
from typing import Dict, List

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None


def chunk_text(text: str, max_chars: int = 1800, overlap: int = 200) -> List[str]:
    s = (text or "").strip()
    if not s:
        return []
    out = []
    i = 0
    while i < len(s):
        end = min(len(s), i + max_chars)
        chunk = s[i:end]
        out.append(chunk.strip())
        if end == len(s):
            break
        i = max(0, end - overlap)
    return [c for c in out if c]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Path to the guideline PDF")
    ap.add_argument("--specialty", default="dry_eye")
    ap.add_argument("--title", required=True)
    ap.add_argument("--version", default="")
    ap.add_argument("--year", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if fitz is None:
        raise SystemExit("PyMuPDF is not installed. Add pymupdf to requirements.")

    pdf_path = args.pdf
    if not os.path.exists(pdf_path):
        raise SystemExit("PDF not found")

    specialty = args.specialty.strip() or "dry_eye"
    title = args.title.strip()
    version = args.version.strip()
    year = int(args.year) if str(args.year).strip().isdigit() else None

    safe_title = "".join([c for c in title if c.isalnum() or c in (" ", "_", ".")]).strip().replace(" ", "_")
    safe_ver = "".join([c for c in version if c.isalnum() or c in (" ", "_", ".")]).strip().replace(" ", "_")
    base = safe_title + ("_" + safe_ver if safe_ver else "")

    out_path = args.out.strip() or os.path.join("guidelines", "packs", specialty, base + ".jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    doc = fitz.open(pdf_path)
    records = []
    for page_i in range(len(doc)):
        page = doc.load_page(page_i)
        text = page.get_text("text") or ""
        chunks = chunk_text(text)
        for ci, chunk in enumerate(chunks, start=1):
            citation_id = f"{specialty}:{safe_title}:{safe_ver or 'v'}:{page_i+1}:{ci}:{uuid.uuid4().hex[:8]}"
            records.append({
                "citation_id": citation_id,
                "specialty": specialty,
                "title": title,
                "version": version,
                "year": year,
                "section": "",
                "page": page_i + 1,
                "chunk_index": ci,
                "text": chunk,
            })

    with open(out_path, "w", encoding="utf8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Wrote {len(records)} chunks to {out_path}")


if __name__ == "__main__":
    main()
