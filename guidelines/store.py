import os
import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _db_path() -> str:
    return (os.getenv("GUIDELINE_DB_PATH") or "guidelines/guidelines.sqlite").strip()


def ensure_db() -> None:
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS guideline_chunks (
                citation_id TEXT PRIMARY KEY,
                specialty TEXT,
                title TEXT,
                version TEXT,
                year INTEGER,
                section TEXT,
                page INTEGER,
                chunk_index INTEGER,
                text TEXT,
                preview TEXT,
                embedding TEXT
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_specialty ON guideline_chunks(specialty)")
        con.commit()
    finally:
        con.close()


@dataclass
class Chunk:
    citation_id: str
    specialty: str
    title: str
    version: str
    year: int
    section: str
    page: int
    chunk_index: int
    text: str
    preview: str
    embedding: List[float]


def upsert_chunks(chunks: List[Chunk]) -> int:
    ensure_db()
    con = sqlite3.connect(_db_path())
    try:
        n = 0
        for c in chunks:
            con.execute(
                """
                INSERT INTO guideline_chunks(
                    citation_id, specialty, title, version, year, section, page, chunk_index, text, preview, embedding
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(citation_id) DO UPDATE SET
                    specialty=excluded.specialty,
                    title=excluded.title,
                    version=excluded.version,
                    year=excluded.year,
                    section=excluded.section,
                    page=excluded.page,
                    chunk_index=excluded.chunk_index,
                    text=excluded.text,
                    preview=excluded.preview,
                    embedding=excluded.embedding
                """,
                (
                    c.citation_id,
                    c.specialty,
                    c.title,
                    c.version,
                    int(c.year or 0),
                    c.section,
                    int(c.page or 0),
                    int(c.chunk_index or 0),
                    c.text,
                    c.preview,
                    json.dumps(c.embedding),
                ),
            )
            n += 1
        con.commit()
        return n
    finally:
        con.close()


def list_references_for_citations(citation_ids: List[str]) -> List[Dict[str, Any]]:
    if not citation_ids:
        return []
    ensure_db()
    con = sqlite3.connect(_db_path())
    try:
        out: List[Dict[str, Any]] = []
        for cid in citation_ids:
            row = con.execute(
                """
                SELECT citation_id, specialty, title, version, year, section, page
                FROM guideline_chunks
                WHERE citation_id=?
                """,
                (cid,),
            ).fetchone()
            if not row:
                continue
            out.append(
                {
                    "citation_id": row[0],
                    "specialty": row[1],
                    "title": row[2],
                    "version": row[3],
                    "year": row[4],
                    "section": row[5],
                    "page": row[6],
                }
            )
        return out
    finally:
        con.close()


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = float(a[i])
        y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def embed_text(openai_client: Any, text: str) -> Tuple[Optional[List[float]], str]:
    if openai_client is None:
        return None, "OpenAI client not available"
    model = (os.getenv("GUIDELINE_EMBED_MODEL") or "text-embedding-3-large").strip()
    try:
        resp = openai_client.embeddings.create(model=model, input=text)
        vec = resp.data[0].embedding
        return list(vec), ""
    except Exception as e:
        return None, str(e)


def search_chunks(openai_client: Any, specialty: str, query: str, k: int = 8) -> Tuple[List[Chunk], str]:
    specialty = (specialty or "").strip().lower()
    if not specialty:
        return [], "Missing specialty"
    ensure_db()
    qvec, err = embed_text(openai_client, query)
    if not qvec:
        return [], err or "Embedding failed"

    con = sqlite3.connect(_db_path())
    try:
        rows = con.execute(
            """
            SELECT citation_id, specialty, title, version, year, section, page, chunk_index, text, preview, embedding
            FROM guideline_chunks
            WHERE lower(specialty)=?
            """,
            (specialty,),
        ).fetchall()

        scored: List[Tuple[float, Chunk]] = []
        for r in rows:
            try:
                emb = json.loads(r[10] or "[]")
            except Exception:
                emb = []
            c = Chunk(
                citation_id=r[0],
                specialty=r[1] or "",
                title=r[2] or "",
                version=r[3] or "",
                year=int(r[4] or 0),
                section=r[5] or "",
                page=int(r[6] or 0),
                chunk_index=int(r[7] or 0),
                text=r[8] or "",
                preview=r[9] or "",
                embedding=emb if isinstance(emb, list) else [],
            )
            s = _cosine(qvec, c.embedding)
            scored.append((s, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[: max(1, int(k or 8))]], ""
    finally:
        con.close()
