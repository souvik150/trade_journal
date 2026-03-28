from __future__ import annotations

from fastapi import APIRouter, Query

from app.services.trade_journal import get_or_build_instrument_report


router = APIRouter(tags=["trade"])


@router.get("/trade")
def trade_detail_by_instrument(date: str = Query(...), instrument: str = Query(...)):
    return get_or_build_instrument_report(date, instrument)
