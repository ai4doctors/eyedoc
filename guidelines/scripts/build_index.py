import json, os
from pathlib import Path
from typing import List, Dict, Any

from guidelines.ingest import ingest_pack
from guidelines.store import Chunk, upsert_chunks, ensure_db

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

def embed_texts(texts: List[str]) -> List[List[float]]:
    model = (os.getenv("OPENAI_EMBED_MODEL") or "text-embedding-3-small").strip()
    if OpenAI is None:
        raise RuntimeError("OpenAI client not available")
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    client = OpenAI(api_key=api_key)
    resp = client.embeddings.create(model=model, input=texts)
    return [d.embedding for d in resp.data]

def make_citation_id(source_id: str, version: str, page: int, chunk_index: int) -> str:
    v = (version or "v1").strip()
    return f"{source_id}:{v}:p{page}:c{chunk_index}"

def main():
    root = Path(__file__).resolve().parents[1]
    packs_dir = root / "packs"
    ensure_db()
    all_rows: List[Dict[str, Any]] = []

    for pack in packs_dir.iterdir():
        if not pack.is_dir():
            continue
        rows = ingest_pack(str(pack))
        all_rows.extend(rows)

    if not all_rows:
        print("no packs to ingest")
        return

    batch_size = int(os.getenv("EMBED_BATCH_SIZE", "32") or "32")
    chunks: List[Chunk] = []
    i = 0
    while i < len(all_rows):
        batch = all_rows[i:i+batch_size]
        texts = [r["text"] for r in batch]
        embs = embed_texts(texts)
        for r, e in zip(batch, embs):
            source_id = r.get("source_id") or "source"
            title = r.get("title") or ""
            version = r.get("edition") or "v1"
            year = int(r.get("year") or 0)
            section = r.get("section") or ""
            page = int(r.get("page") or 0)
            chunk_index = int(r.get("chunk_index") or 0)
            citation_id = make_citation_id(source_id, version, page, chunk_index)
            preview = (r.get("text") or "")[:220]
            chunks.append(Chunk(
                citation_id=citation_id,
                specialty=(os.getenv("CANONICAL_SPECIALTY") or "ophthalmology").strip(),
                title=title,
                version=version,
                year=year,
                section=section,
                page=page,
                chunk_index=chunk_index,
                text=r.get("text") or "",
                preview=preview,
                embedding=e,
            ))
        i += batch_size

    n = upsert_chunks(chunks)
    print("upserted", n)

if __name__ == "__main__":
    main()
