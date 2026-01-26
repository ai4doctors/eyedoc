"""PDF and upload text extraction.

This is intentionally minimal. Keep your current OCR and extraction pipeline here.
"""
from __future__ import annotations

from typing import BinaryIO, Tuple

def extract_text_from_upload(file_stream: BinaryIO, filename: str) -> Tuple[str, dict]:
    raise NotImplementedError("Implement extraction and return (text, meta).")

def export_pdf_document(html: str, output_path: str) -> str:
    raise NotImplementedError("Implement HTML to PDF export and return output path.")
