from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.trade_journal import get_time_performance


router = APIRouter(tags=["analytics"])


@router.get("/analytics/time-performance")
def analytics_time_performance(
    date: str | None = Query(None),
    year: int | None = Query(None),
    month: int | None = Query(None),
):
    return get_time_performance(date, year, month)
