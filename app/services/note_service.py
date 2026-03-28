from __future__ import annotations

import io
import os
import time

from fastapi import HTTPException, UploadFile
from openai import OpenAI

from app.db import mongo_store


def transcribe_voice_note(audio_file: UploadFile) -> tuple[str, str]:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is required for voice transcription.")
    model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    client = OpenAI()
    started_at = time.perf_counter()
    audio_file.file.seek(0)
    content = audio_file.file.read()
    named_buffer = io.BytesIO(content)
    named_buffer.name = audio_file.filename or "voice_note.wav"
    try:
        transcript = client.audio.transcriptions.create(model=model, file=named_buffer)
    except Exception as exc:
        mongo_store.log_llm_call(
            operation="voice_transcription",
            model=model,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            success=False,
            error=str(exc),
        )
        raise
    mongo_store.log_llm_call(
        operation="voice_transcription",
        model=model,
        duration_ms=(time.perf_counter() - started_at) * 1000,
        success=True,
    )
    text = (getattr(transcript, "text", None) or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI returned an empty transcription.")
    return text, model
