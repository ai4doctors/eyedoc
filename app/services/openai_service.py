"""OpenAI wrapper.

Replace with your existing Maneiro analysis and report generation logic.
"""
from __future__ import annotations

from typing import Any, Dict

def analyze_note(text: str, specialty: str | None = None) -> Dict[str, Any]:
    raise NotImplementedError("Implement analysis. Return structured JSON used by the UI.")

def generate_report(analysis: Dict[str, Any], template: str) -> Dict[str, Any]:
    raise NotImplementedError("Implement report generation. Return HTML or rich text plus metadata.")
