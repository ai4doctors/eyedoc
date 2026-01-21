import os
import re
import json
import math
import sqlite3
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

DB_DEFAULT = os.path.join("data", "guidelines", "index.sqlite")


@dataclass
class Passage:
    id: int
    specialty: str
    source: str
    page_start: int
    page_end: int
    text: str
    score: float


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(len(a)):
        dot += a[i] * b[i]
        na += a[i] * a[i]
        nb += b[i] * b[i]
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def open_db(db_path: str = DB_DEFAULT) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return sqlite3.connect(db_path)


def ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS passages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            specialty TEXT NOT NULL,
            source TEXT NOT NULL,
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL,
            text TEXT NOT NULL,
            preview TEXT NOT NULL,
            embedding_json TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_passages_specialty ON passages(specialty);")
    conn.commit()


def embed_text(text: str) -> List[float]:
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not installed")
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    model = (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip()
    client = OpenAI(api_key=api_key)
    res = client.embeddings.create(model=model, input=text)
    return list(res.data[0].embedding)


def extract_pdf_text(pdf_path: str) -> List[Tuple[int, str]]:
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not available")
    doc = fitz.open(pdf_path)
    pages: List[Tuple[int, str]] = []
    for i in range(doc.page_count):
        t = doc.load_page(i).get_text("text") or ""
        t = _norm_ws(t)
        if t:
            pages.append((i + 1, t))
    doc.close()
    return pages


def chunk_pages(pages: List[Tuple[int, str]], max_chars: int = 1800, overlap_chars: int = 200) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    buf = ""
    start_page: Optional[int] = None
    last_page: Optional[int] = None

    def flush() -> None:
        nonlocal buf, start_page, last_page
        text = _norm_ws(buf)
        if text and start_page is not None and last_page is not None:
            chunks.append({
                "page_start": int(start_page),
                "page_end": int(last_page),
                "text": text,
            })
        if overlap_chars > 0 and text:
            buf = text[-overlap_chars:]
        else:
            buf = ""
        start_page = None
        last_page = None

    for pno, text in pages:
        if start_page is None:
            start_page = pno
        last_page = pno
        if not buf:
            buf = text
        else:
            buf = buf + "
" + text
        if len(buf) >= max_chars:
            flush()

    if buf:
        flush()

    return chunks


def add_pdf(conn: sqlite3.Connection, specialty: str, pdf_path: str, source_name: Optional[str] = None) -> int:
    ensure_tables(conn)
    source_name = source_name or os.path.basename(pdf_path)

    pages = extract_pdf_text(pdf_path)
    chunks = chunk_pages(pages)

    inserted = 0
    cur = conn.cursor()
    for ch in chunks:
        txt = ch["text"]
        emb = embed_text(txt)
        preview = txt[:240]
        cur.execute(
            "INSERT INTO passages (specialty, source, page_start, page_end, text, preview, embedding_json) VALUES (?,?,?,?,?,?,?)",
            (
                specialty,
                source_name,
                int(ch["page_start"]),
                int(ch["page_end"]),
                txt,
                preview,
                json.dumps(emb),
            ),
        )
        inserted += 1

    conn.commit()
    return inserted


def list_sources(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT source, specialty, MIN(page_start), MAX(page_end), COUNT(1) FROM passages GROUP BY source, specialty ORDER BY specialty, source"
    )
    out = []
    for r in cur.fetchall():
        out.append({
            "source": r[0],
            "specialty": r[1],
            "page_start": r[2],
            "page_end": r[3],
            "chunks": r[4],
        })
    return out


def search(conn: sqlite3.Connection, specialty: str, query: str, top_k: int = 6) -> List[Passage]:
    q_emb = embed_text(query)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, source, page_start, page_end, text, embedding_json FROM passages WHERE specialty=?",
        (specialty,),
    )
    rows = cur.fetchall()

    scored: List[Passage] = []
    for r in rows:
        emb = json.loads(r[5])
        s = _cosine(q_emb, emb)
        scored.append(Passage(
            id=r[0],
            specialty=specialty,
            source=r[1],
            page_start=r[2],
            page_end=r[3],
            text=r[4],
            score=float(s),
        ))

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[: int(top_k)]
