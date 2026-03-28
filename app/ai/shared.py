from __future__ import annotations

import json
import os
import time

from crewai import LLM
from pydantic import BaseModel

from app.db import mongo_store


class SignalEvent(BaseModel):
    time: str
    title: str
    description: str
    tag: str | None = None


class TradeAnalysisResult(BaseModel):
    signal_timeline: list[SignalEvent] = []
    why_it_worked: str | None = None
    what_could_be_better: str | None = None
    post_exit_summary: str | None = None
    lessons: list[str] = []
    trade_narrative: str | None = None
    strategy: str | None = None
    emotion: str | None = None


class TradeTagResult(BaseModel):
    strategy: str | None = None
    emotion: str | None = None


def llm() -> LLM:
    return LLM(model="gpt-4o-mini", temperature=0.3)


def record_llm_metric(*, operation: str, started_at: float, success: bool, error: str | None = None) -> None:
    mongo_store.log_llm_call(
        operation=operation,
        model="gpt-4o-mini",
        duration_ms=(time.perf_counter() - started_at) * 1000,
        success=success,
        error=error,
    )


def no_key_trade_result() -> dict:
    return {
        "signal_timeline": [],
        "strategy": None,
        "emotion": None,
        "analysis": {
            "why_it_worked": None,
            "what_could_be_better": None,
            "post_exit_summary": None,
            "lessons": [],
            "trade_narrative": None,
        },
    }


def parse_trade_result(raw: str) -> dict:
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(raw[start:end])
            return {
                "signal_timeline": data.get("signal_timeline", []),
                "strategy": data.get("strategy"),
                "emotion": data.get("emotion"),
                "analysis": {
                    "why_it_worked": data.get("why_it_worked"),
                    "what_could_be_better": data.get("what_could_be_better"),
                    "post_exit_summary": data.get("post_exit_summary"),
                    "lessons": data.get("lessons", []),
                    "trade_narrative": data.get("trade_narrative"),
                },
            }
    except Exception:
        pass
    return no_key_trade_result()


def parse_trade_tag_result(raw: str) -> dict:
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(raw[start:end])
            return {
                "strategy": data.get("strategy"),
                "emotion": data.get("emotion"),
            }
    except Exception:
        pass
    return {"strategy": None, "emotion": None}


def has_openai_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))
