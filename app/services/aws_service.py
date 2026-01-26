"""AWS operations wrapper.

Replace stubs with your existing S3 and Transcribe logic.
"""
from __future__ import annotations

from typing import Any, Dict

def start_transcription(job_id: str, s3_uri: str, language_code: str = "en-US") -> Dict[str, Any]:
    raise NotImplementedError("Wire this to AWS Transcribe. Return job metadata.")

def get_transcription_status(job_id: str) -> Dict[str, Any]:
    raise NotImplementedError("Wire this to AWS Transcribe. Return status and transcript location.")
