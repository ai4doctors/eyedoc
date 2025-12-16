from __future__ import annotations
from typing import Tuple, Dict, Any

from pypdf import PdfReader
import io

def extract_text_from_pdf(pdf_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    """
    Extract text from a PDF using pypdf.
    Notes:
      - Scanned PDFs will yield little or no text. The UI should warn the user.
      - OCR is intentionally not enabled by default to keep Render dependencies simple.
    """
    meta: Dict[str, Any] = {"pages": 0, "chars": 0, "scanned_suspected": False}
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        meta["pages"] = len(reader.pages)
        parts = []
        for p in reader.pages:
            t = p.extract_text() or ""
            if t:
                parts.append(t)
        text = "\n\n".join(parts).strip()
        meta["chars"] = len(text)
        # Heuristic: if very low chars per page, likely scanned
        if meta["pages"] and (meta["chars"] / max(meta["pages"], 1) < 200):
            meta["scanned_suspected"] = True
        return text, meta
    except Exception as e:
        return "", {"pages": 0, "chars": 0, "scanned_suspected": True, "error": str(e)}
