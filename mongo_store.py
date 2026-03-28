from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from typing import Any


logger = logging.getLogger(__name__)

_client = None
_db = None
_initialised = False


def _init() -> None:
    global _client, _db, _initialised
    if _initialised:
        return
    _initialised = True

    try:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
    except Exception as exc:
        logger.warning("pymongo unavailable; Mongo persistence disabled: %s", exc)
        return

    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("MONGO_DB_NAME", "trade_journal")

    try:
        _client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        _client.admin.command("ping")
        _db = _client[db_name]
        _db.orders_by_date.create_index("date", unique=True)
        _db.daily_reports.create_index("date", unique=True)
        _db.daily_pnl_flow.create_index("date", unique=True)
        _db.instrument_reports.create_index("cache_key", unique=True)
        _db.notes.create_index([("date", 1), ("instrument", 1)], unique=True)
        _db.yearly_pnl.create_index("year", unique=True)
    except PyMongoError as exc:
        logger.warning("Mongo unavailable; persistence disabled: %s", exc)
        _client = None
        _db = None


def is_available() -> bool:
    _init()
    return _db is not None


def save_daily_report(
    *,
    date: str,
    report: dict[str, Any],
    source_order_count: int,
    ai_generated: bool,
) -> None:
    _init()
    if _db is None:
        return
    _db.daily_reports.replace_one(
        {"date": date},
        {
            "date": date,
            "source_order_count": source_order_count,
            "ai_generated": ai_generated,
            "report": report,
        },
        upsert=True,
    )


def get_daily_report(date: str) -> dict[str, Any] | None:
    _init()
    if _db is None:
        return None
    doc = _db.daily_reports.find_one({"date": date}, {"_id": 0})
    return doc if isinstance(doc, dict) else None


def save_daily_pnl_flow(
    *,
    date: str,
    report: dict[str, Any],
    source_trade_count: int,
) -> None:
    _init()
    if _db is None:
        return
    _db.daily_pnl_flow.replace_one(
        {"date": date},
        {
            "date": date,
            "source_trade_count": source_trade_count,
            "report": report,
        },
        upsert=True,
    )


def get_daily_pnl_flow(date: str) -> dict[str, Any] | None:
    _init()
    if _db is None:
        return None
    doc = _db.daily_pnl_flow.find_one({"date": date}, {"_id": 0})
    return doc if isinstance(doc, dict) else None


def save_instrument_report(
    *,
    cache_key: str,
    date: str,
    instrument: str,
    report: dict[str, Any],
    source_trade_count: int,
    ai_generated: bool,
) -> None:
    _init()
    if _db is None:
        return
    _db.instrument_reports.replace_one(
        {"cache_key": cache_key},
        {
            "cache_key": cache_key,
            "date": date,
            "instrument": instrument,
            "source_trade_count": source_trade_count,
            "ai_generated": ai_generated,
            "report": report,
        },
        upsert=True,
    )


def get_instrument_report(cache_key: str) -> dict[str, Any] | None:
    _init()
    if _db is None:
        return None
    doc = _db.instrument_reports.find_one({"cache_key": cache_key}, {"_id": 0})
    return doc if isinstance(doc, dict) else None


def save_orders_for_date(*, date: str, orders: list[dict[str, Any]]) -> None:
    _init()
    if _db is None:
        return
    _db.orders_by_date.replace_one(
        {"date": date},
        {
            "date": date,
            "order_count": len(orders),
            "orders": orders,
        },
        upsert=True,
    )


def get_all_orders_by_date() -> dict[str, list[dict[str, Any]]]:
    _init()
    if _db is None:
        return {}
    docs = _db.orders_by_date.find({}, {"_id": 0, "date": 1, "orders": 1})
    return {
        doc["date"]: doc.get("orders", [])
        for doc in docs
        if isinstance(doc, dict) and "date" in doc
    }


def clear_orders() -> None:
    _init()
    if _db is None:
        return
    _db.orders_by_date.delete_many({})


def save_yearly_pnl(
    *,
    year: int,
    report: dict[str, Any],
    source_date_count: int,
    source_order_count: int,
) -> None:
    _init()
    if _db is None:
        return
    _db.yearly_pnl.replace_one(
        {"year": year},
        {
            "year": year,
            "source_date_count": source_date_count,
            "source_order_count": source_order_count,
            "report": report,
        },
        upsert=True,
    )


def get_yearly_pnl(year: int) -> dict[str, Any] | None:
    _init()
    if _db is None:
        return None
    doc = _db.yearly_pnl.find_one({"year": year}, {"_id": 0})
    return doc if isinstance(doc, dict) else None


def create_note(
    *,
    date: str,
    instrument: str,
    text: str,
    source: str,
    audio_filename: str | None = None,
    transcription_model: str | None = None,
) -> dict[str, Any] | None:
    _init()
    if _db is None:
        return None

    doc = {
        "date": date,
        "instrument": instrument,
        "text": text,
        "source": source,
        "audio_filename": audio_filename,
        "transcription_model": transcription_model,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        existing = _db.notes.find_one(
            {"date": date, "instrument": instrument},
            {"_id": 1, "created_at": 1},
        )
        created_at = (
            existing.get("created_at")
            if isinstance(existing, dict) and existing.get("created_at")
            else doc["updated_at"]
        )
        doc["created_at"] = created_at
        _db.notes.replace_one(
            {"date": date, "instrument": instrument},
            doc,
            upsert=True,
        )
        saved = _db.notes.find_one(
            {"date": date, "instrument": instrument},
            {"_id": 1, "date": 1, "instrument": 1, "text": 1, "source": 1, "audio_filename": 1, "transcription_model": 1, "created_at": 1, "updated_at": 1},
        )
    except Exception as exc:
        logger.warning("Failed to upsert note for %s %s: %s", date, instrument, exc)
        return None

    if not isinstance(saved, dict):
        return None
    return {
        "note_id": str(saved["_id"]),
        "date": saved["date"],
        "instrument": saved["instrument"],
        "text": saved["text"],
        "source": saved["source"],
        "audio_filename": saved.get("audio_filename"),
        "transcription_model": saved.get("transcription_model"),
        "created_at": saved["created_at"],
        "updated_at": saved.get("updated_at"),
    }


def get_note(*, date: str, instrument: str) -> dict[str, Any] | None:
    _init()
    if _db is None:
        return None

    try:
        doc = _db.notes.find_one(
            {"date": date, "instrument": instrument},
            {"_id": 1, "date": 1, "instrument": 1, "text": 1, "source": 1, "audio_filename": 1, "transcription_model": 1, "created_at": 1, "updated_at": 1},
        )
    except Exception as exc:
        logger.warning("Failed to fetch note for %s %s: %s", date, instrument, exc)
        return None

    if not isinstance(doc, dict):
        return None

    return {
        "note_id": str(doc["_id"]),
        "date": doc["date"],
        "instrument": doc["instrument"],
        "text": doc["text"],
        "source": doc["source"],
        "audio_filename": doc.get("audio_filename"),
        "transcription_model": doc.get("transcription_model"),
        "created_at": doc["created_at"],
        "updated_at": doc.get("updated_at"),
    }
