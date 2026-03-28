from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.db import mongo_store
from app.services.note_service import transcribe_voice_note
from app.services.trade_journal import ensure_instrument_exists, normalize_instrument


router = APIRouter(tags=["notes"])


@router.get("/notes")
def get_notes(date: str = Query(...), instrument: str = Query(...)):
    ensure_instrument_exists(date, instrument)
    normalized_instrument = normalize_instrument(instrument)
    return {
        "date": date,
        "instrument": normalized_instrument,
        "note": mongo_store.get_note(date=date, instrument=normalized_instrument),
    }


@router.post("/notes/text")
def create_text_note(date: str = Form(...), instrument: str = Form(...), text: str = Form(...)):
    ensure_instrument_exists(date, instrument)
    normalized_instrument = normalize_instrument(instrument)
    cleaned_text = text.strip()
    if not cleaned_text:
        raise HTTPException(status_code=400, detail="Note text cannot be empty.")
    note = mongo_store.create_note(date=date, instrument=normalized_instrument, text=cleaned_text, source="text")
    if note is None:
        raise HTTPException(status_code=503, detail="MongoDB is not available.")
    return {"status": "saved", "note": note}


@router.post("/notes/voice")
def create_voice_note(date: str = Form(...), instrument: str = Form(...), file: UploadFile = File(...)):
    ensure_instrument_exists(date, instrument)
    normalized_instrument = normalize_instrument(instrument)
    transcript_text, model = transcribe_voice_note(file)
    note = mongo_store.create_note(
        date=date,
        instrument=normalized_instrument,
        text=transcript_text,
        source="voice",
        audio_filename=file.filename,
        transcription_model=model,
    )
    if note is None:
        raise HTTPException(status_code=503, detail="MongoDB is not available.")
    return {"status": "saved", "note": note}
