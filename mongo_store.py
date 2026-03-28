from __future__ import annotations

from collections import defaultdict
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
        _db.api_request_metrics.create_index("created_at")
        _db.api_request_metrics.create_index([("path", 1), ("created_at", -1)])
        _db.llm_call_metrics.create_index("created_at")
        _db.llm_call_metrics.create_index([("operation", 1), ("created_at", -1)])
        _db.yearly_pnl.create_index("year", unique=True)
    except PyMongoError as exc:
        logger.warning("Mongo unavailable; persistence disabled: %s", exc)
        _client = None
        _db = None


def is_available() -> bool:
    _init()
    return _db is not None


def log_api_request(
    *,
    method: str,
    path: str,
    query: str,
    status_code: int,
    duration_ms: float,
    error: str | None = None,
) -> None:
    _init()
    if _db is None:
        return
    try:
        _db.api_request_metrics.insert_one(
            {
                "method": method,
                "path": path,
                "query": query,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "error": error,
                "created_at": datetime.now(timezone.utc),
            }
        )
    except Exception as exc:
        logger.warning("Failed to log API request metric for %s %s: %s", method, path, exc)


def log_llm_call(
    *,
    operation: str,
    model: str,
    duration_ms: float,
    success: bool,
    error: str | None = None,
) -> None:
    _init()
    if _db is None:
        return
    try:
        _db.llm_call_metrics.insert_one(
            {
                "operation": operation,
                "model": model,
                "duration_ms": round(duration_ms, 2),
                "success": success,
                "error": error,
                "created_at": datetime.now(timezone.utc),
            }
        )
    except Exception as exc:
        logger.warning("Failed to log LLM metric for %s: %s", operation, exc)


def get_monitoring_summary(*, window_minutes: int = 60) -> dict[str, Any]:
    _init()
    since = datetime.now(timezone.utc).timestamp() - (window_minutes * 60)
    since_dt = datetime.fromtimestamp(since, tz=timezone.utc)

    if _db is None:
        return {
            "window_minutes": window_minutes,
            "mongo_connected": False,
            "api": {"total_requests": 0, "avg_latency_ms": None, "failure_count": 0, "routes": []},
            "llm": {"total_calls": 0, "avg_latency_ms": None, "failure_count": 0, "operations": []},
            "alerts": [{"severity": "critical", "message": "MongoDB is unavailable; telemetry persistence is disabled."}],
        }

    api_docs = list(_db.api_request_metrics.find({"created_at": {"$gte": since_dt}}, {"_id": 0}))
    llm_docs = list(_db.llm_call_metrics.find({"created_at": {"$gte": since_dt}}, {"_id": 0}))

    route_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "requests": 0,
            "failure_count": 0,
            "total_latency_ms": 0.0,
            "max_latency_ms": 0.0,
            "last_status_code": None,
        }
    )
    for doc in api_docs:
        path = doc.get("path", "unknown")
        bucket = route_buckets[path]
        bucket["requests"] += 1
        duration = float(doc.get("duration_ms", 0.0))
        bucket["total_latency_ms"] += duration
        bucket["max_latency_ms"] = max(bucket["max_latency_ms"], duration)
        status_code = int(doc.get("status_code", 0))
        bucket["last_status_code"] = status_code
        if status_code >= 500:
            bucket["failure_count"] += 1

    routes = []
    for path, bucket in sorted(route_buckets.items()):
        avg_latency = round(bucket["total_latency_ms"] / bucket["requests"], 2) if bucket["requests"] else None
        routes.append(
            {
                "path": path,
                "requests": bucket["requests"],
                "failure_count": bucket["failure_count"],
                "avg_latency_ms": avg_latency,
                "max_latency_ms": round(bucket["max_latency_ms"], 2),
                "last_status_code": bucket["last_status_code"],
            }
        )

    llm_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "failure_count": 0,
            "total_latency_ms": 0.0,
            "last_model": None,
        }
    )
    for doc in llm_docs:
        operation = doc.get("operation", "unknown")
        bucket = llm_buckets[operation]
        bucket["calls"] += 1
        bucket["total_latency_ms"] += float(doc.get("duration_ms", 0.0))
        bucket["last_model"] = doc.get("model")
        if not doc.get("success", False):
            bucket["failure_count"] += 1

    operations = []
    for operation, bucket in sorted(llm_buckets.items()):
        avg_latency = round(bucket["total_latency_ms"] / bucket["calls"], 2) if bucket["calls"] else None
        operations.append(
            {
                "operation": operation,
                "calls": bucket["calls"],
                "failure_count": bucket["failure_count"],
                "avg_latency_ms": avg_latency,
                "model": bucket["last_model"],
            }
        )

    api_avg_latency = round(sum(float(doc.get("duration_ms", 0.0)) for doc in api_docs) / len(api_docs), 2) if api_docs else None
    llm_avg_latency = round(sum(float(doc.get("duration_ms", 0.0)) for doc in llm_docs) / len(llm_docs), 2) if llm_docs else None

    alerts: list[dict[str, str]] = []
    for route in routes:
        if route["failure_count"] > 0:
            alerts.append({"severity": "critical", "message": f"{route['path']} has {route['failure_count']} failing request(s) in the last {window_minutes} minutes."})
        elif route["avg_latency_ms"] is not None and route["avg_latency_ms"] > 1500:
            alerts.append({"severity": "warning", "message": f"{route['path']} average latency is {route['avg_latency_ms']} ms."})

    for operation in operations:
        if operation["failure_count"] > 0:
            alerts.append({"severity": "critical", "message": f"LLM workflow {operation['operation']} has {operation['failure_count']} failure(s) in the last {window_minutes} minutes."})

    return {
        "window_minutes": window_minutes,
        "mongo_connected": True,
        "api": {
            "total_requests": len(api_docs),
            "avg_latency_ms": api_avg_latency,
            "failure_count": sum(1 for doc in api_docs if int(doc.get("status_code", 0)) >= 500),
            "routes": routes,
        },
        "llm": {
            "total_calls": len(llm_docs),
            "avg_latency_ms": llm_avg_latency,
            "failure_count": sum(1 for doc in llm_docs if not doc.get("success", False)),
            "operations": operations,
        },
        "alerts": alerts,
    }


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
