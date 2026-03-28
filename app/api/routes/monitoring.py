from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.core import store
from app.db import mongo_store
from app.services.monitoring_service import get_alerts, get_summary, render_dashboard_html


router = APIRouter(tags=["monitoring"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "dates_loaded": len(store.orders_by_date),
        "reports_cached": len(store.daily_reports_by_date),
        "yearly_cached": len(store.yearly_reports_by_year),
        "mongo_connected": mongo_store.is_available(),
        "symbols_with_ohlcv": len(store.md_data),
    }


@router.get("/monitoring/summary")
def monitoring_summary(window_minutes: int = Query(60, ge=1, le=1440)):
    return get_summary(window_minutes)


@router.get("/monitoring/alerts")
def monitoring_alerts(window_minutes: int = Query(60, ge=1, le=1440)):
    return get_alerts(window_minutes)


@router.get("/monitoring", response_class=HTMLResponse)
def monitoring_dashboard(window_minutes: int = Query(60, ge=1, le=1440)):
    return render_dashboard_html(window_minutes)
