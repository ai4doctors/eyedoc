import json, re, os
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

try:
    import fitz
except Exception:
    fitz = None

def extract_pdf_text_with_pages(pdf_path: str) -> List[Tuple[int, str]]:
    if fitz is None:
        raise RuntimeError("PyMuPDF not available")
    doc = fitz.open(pdf_path)
    out = []
    for i in range(len(doc)):
        page = doc.load_page(i)
        txt = (page.get_text("text") or "").strip()
        out.append((i + 1, txt))
    return out

def chunk_text(text: str, max_chars: int = 1200) -> List[str]:
    s = re.sub(r"\s+", " ", (text or "")).strip()
    if not s:
        return []
    chunks = []
    i = 0
    while i < len(s):
        chunks.append(s[i:i+max_chars])
        i += max_chars
    return chunks

def ingest_pack(pack_dir: str) -> List[Dict[str, Any]]:
    p = Path(pack_dir)
    meta_path = p / "meta.json"
    pdf_path = p / "source.pdf"
    if not meta_path.exists() or not pdf_path.exists():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf8"))
    pages = extract_pdf_text_with_pages(str(pdf_path))
    rows = []
    for page_num, page_txt in pages:
        for c_idx, chunk in enumerate(chunk_text(page_txt)):
            rows.append({
                "source_id": meta.get("id") or p.name,
                "title": meta.get("title") or "",
                "organization": meta.get("organization") or "",
                "edition": meta.get("edition") or "",
                "year": meta.get("year"),
                "page": page_num,
                "section": meta.get("default_section") or "",
                "chunk_index": c_idx,
                "text": chunk,
            })
    return rows

def ingest_packs(packs_dir: str, out_dir: str) -> None:
    packs = [x for x in Path(packs_dir).iterdir() if x.is_dir()]
    all_rows = []
    for pack in packs:
        all_rows.extend(ingest_pack(str(pack)))
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir)/"chunks.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows), encoding="utf8")
    print("chunks", len(all_rows))
