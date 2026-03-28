from __future__ import annotations

from fastapi import APIRouter

from app.core import store
from app.services.runtime import bootstrap_data


router = APIRouter(tags=["admin"])


@router.post("/data/load")
def load_data():
    bootstrap_data()
    return {
        "status": "rebuilt",
        "dates_loaded": len(store.orders_by_date),
        "reports_cached": len(store.daily_reports_by_date),
        "symbols_with_ohlcv": store.all_symbols(),
        "total_orders": sum(len(v) for v in store.orders_by_date.values()),
    }
