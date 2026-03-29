from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import HTTPException

from app.ai.crews import DailyJournalCrew, InstrumentAnalysisCrew, PatternInsightCrew, TradeTaggingCrew
from app.core import store
from app.db import mongo_store
from app.analytics.trade_metrics import daily_stats, monthly_summary, pair_trades, pnl_flow, time_window_stats
from app.integrations.intraday import candles_to_csv, get_intraday_candles


logger = logging.getLogger(__name__)


def normalize_instrument(instrument: str) -> str:
    return instrument.strip().upper()


def trade_tag_enrichment(trade: dict, trade_date: str) -> dict:
    return TradeTaggingCrew(trade, trade_date).run()


def build_public_trade(trade: dict, trade_no: int, trade_date: str) -> dict:
    tag_result = trade_tag_enrichment(trade, trade_date)
    public_trade = {
        "trade_no": trade_no,
        "instrument": trade["instrument"],
        "asset": trade["asset"],
        "asset_type": trade["asset_type"],
        "derivative_type": trade["derivative_type"],
        "direction": trade["direction"],
        "entry_time": trade["entry_time"],
        "entry_price": trade["entry_price"],
        "exit_time": trade["exit_time"],
        "exit_price": trade["exit_price"],
        "qty": trade["qty"],
        "pnl": trade["pnl"],
    }
    if tag_result.get("strategy") is not None:
        public_trade["strategy"] = tag_result["strategy"]
    if tag_result.get("emotion") is not None:
        public_trade["emotion"] = tag_result["emotion"]
    return public_trade


def build_daily_report(date: str, orders: list[dict]) -> dict:
    trades = pair_trades(orders)
    stats = daily_stats(trades)
    public_trades = [build_public_trade(trade, (i - 1) * 2 + 1, date) for i, trade in enumerate(trades, start=1)]
    return {
        "date": date,
        "stats": {
            "net_pnl": stats["net_pnl"],
            "trades": stats["trades"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": stats["win_rate"],
            "best_trade_pnl": stats["best_trade_pnl"],
            "max_drawdown": stats["max_drawdown"],
        },
        "smart_insight": DailyJournalCrew(trades, date, stats).run(),
        "trades": public_trades,
    }


def get_or_build_daily_report(date: str) -> dict | None:
    report = store.get_daily_report(date)
    if report is not None:
        return report
    orders = store.get_orders(date)
    if orders is None:
        return None

    cached = mongo_store.get_daily_report(date)
    if (
        cached is not None
        and cached.get("source_order_count") == len(orders)
        and (cached.get("ai_generated") or not os.getenv("OPENAI_API_KEY"))
        and isinstance(cached.get("report"), dict)
    ):
        report = cached["report"]
    else:
        logger.info("Building daily report cache for %s", date)
        report = build_daily_report(date, orders)
        mongo_store.save_daily_report(
            date=date,
            report=report,
            source_order_count=len(orders),
            ai_generated=bool(os.getenv("OPENAI_API_KEY")),
        )
    store.set_daily_report(date, report)
    return report


def find_instrument_trades(date: str, instrument: str) -> tuple[list[dict], list[dict], dict]:
    orders = store.get_orders(date)
    if orders is None:
        raise HTTPException(status_code=404, detail=f"No orders available for {date}.")

    daily_report = get_or_build_daily_report(date)
    if daily_report is None:
        raise HTTPException(status_code=404, detail=f"No daily report available for {date}.")

    raw_trades = pair_trades(orders)
    normalized_instrument = normalize_instrument(instrument)
    matching_raw_trades = [trade for trade in raw_trades if trade["instrument"].upper() == normalized_instrument]
    matching_public_trades = [trade for trade in daily_report["trades"] if trade["instrument"].upper() == normalized_instrument]
    if not matching_raw_trades or not matching_public_trades:
        raise HTTPException(status_code=404, detail=f"No trades found for instrument {instrument} on {date}.")
    return matching_raw_trades, matching_public_trades, matching_raw_trades[0]


def ensure_instrument_exists(date: str, instrument: str) -> dict:
    orders = store.get_orders(date)
    if orders is None:
        raise HTTPException(status_code=404, detail=f"No orders available for {date}.")
    normalized_instrument = normalize_instrument(instrument)
    matching_orders = [order for order in orders if str(order.get("display_name", "")).upper() == normalized_instrument]
    if not matching_orders:
        raise HTTPException(status_code=404, detail=f"No orders found for instrument {instrument} on {date}.")
    return matching_orders[0]


def instrument_cache_key(date: str, instrument: str) -> str:
    return f"{date}::{normalize_instrument(instrument)}"


def build_instrument_report(date: str, instrument: str) -> dict:
    from app.integrations.intraday import compute_best_entry_exit

    raw_trades, public_trades, instrument_meta = find_instrument_trades(date, instrument)
    stats = daily_stats(raw_trades)
    md = store.get_md(instrument_meta["asset"])
    daily_candles = md["candles"] if md else []

    first_trade = public_trades[0]
    last_trade  = public_trades[-1]
    direction   = first_trade["direction"]

    # Store-first: served from cache on repeat calls, fetched from Nubra on first call
    intraday_candles = get_intraday_candles(
        symbol=instrument_meta["asset"],
        exchange=md["exchange"] if md else "NSE",
        inst_type=instrument_meta["asset_type"].rstrip("S"),
        date=date,
    )

    # Compute best entry/exit deterministically from actual OHLCV — no AI guessing
    best_ee = compute_best_entry_exit(
        candles=intraday_candles,
        direction=direction,
        entry_time=first_trade["entry_time"],
        exit_time=last_trade["exit_time"],
    )

    ai_result = InstrumentAnalysisCrew(
        instrument=instrument_meta["instrument"],
        date=date,
        trades=raw_trades,
        intraday_csv=candles_to_csv(intraday_candles),
        best_entry=best_ee["best_entry"],
        best_exit=best_ee["best_exit"],
    ).run()

    return {
        "date": date,
        "instrument": instrument_meta["instrument"],
        "asset": instrument_meta["asset"],
        "asset_type": instrument_meta["asset_type"],
        "derivative_type": instrument_meta["derivative_type"],
        "direction": direction,
        "entry": {"time": first_trade["entry_time"], "price": first_trade["entry_price"]},
        "exit": {"time": last_trade["exit_time"], "price": last_trade["exit_price"]},
        "best_entry": best_ee["best_entry"],
        "best_exit": best_ee["best_exit"],
        "qty": sum(trade["qty"] for trade in public_trades),
        "pnl": stats["net_pnl"],
        "strategy": ai_result.get("strategy"),
        "emotion": ai_result.get("emotion"),
        "stats": {
            "net_pnl": stats["net_pnl"],
            "trades": stats["trades"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": stats["win_rate"],
            "best_trade_pnl": stats["best_trade_pnl"],
            "max_drawdown": stats["max_drawdown"],
        },
        "intraday_candles": intraday_candles,   # full 1m candles for verification
        "ohlcv_candles": daily_candles,
        "signal_timeline": sorted(ai_result["signal_timeline"], key=lambda e: e.get("time") or ""),
        "analysis": ai_result["analysis"],
        "trades": public_trades,
    }


def get_or_build_instrument_report(date: str, instrument: str) -> dict:
    cache_key = instrument_cache_key(date, instrument)
    report = store.get_instrument_report(cache_key)
    if report is not None:
        return report
    raw_trades, _, _ = find_instrument_trades(date, instrument)
    cached = mongo_store.get_instrument_report(cache_key)
    if (
        cached is not None
        and cached.get("source_trade_count") == len(raw_trades)
        and (cached.get("ai_generated") or not os.getenv("OPENAI_API_KEY"))
        and isinstance(cached.get("report"), dict)
    ):
        report = cached["report"]
    else:
        logger.info("Building instrument report cache for %s on %s", instrument, date)
        report = build_instrument_report(date, instrument)
        mongo_store.save_instrument_report(
            cache_key=cache_key,
            date=date,
            instrument=normalize_instrument(instrument),
            report=report,
            source_trade_count=len(raw_trades),
            ai_generated=bool(os.getenv("OPENAI_API_KEY")),
        )
    store.set_instrument_report(cache_key, report)
    return report


def build_yearly_pnl_report(year: int) -> dict:
    prefix = f"{year}-"
    matching_dates = sorted(date for date in store.orders_by_date if date.startswith(prefix))
    if not matching_dates:
        raise HTTPException(status_code=404, detail=f"No data available for {year}.")
    days = []
    total_trades = 0
    for trade_date in matching_dates:
        trades = pair_trades(store.orders_by_date[trade_date])
        stats = daily_stats(trades)
        total_trades += stats["trades"]
        days.append({
            "date": trade_date,
            "net_pnl": stats["net_pnl"],
            "trades": stats["trades"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": stats["win_rate"],
        })
    trading_days = [day for day in days if day["trades"] > 0]
    best_day = max(trading_days, key=lambda day: day["net_pnl"]) if trading_days else None
    worst_day = min(trading_days, key=lambda day: day["net_pnl"]) if trading_days else None
    return {
        "year": year,
        "summary": {
            "net_pnl": round(sum(day["net_pnl"] for day in days), 2),
            "trading_days": len(trading_days),
            "total_trades": total_trades,
            "winning_days": sum(1 for day in trading_days if day["net_pnl"] > 0),
            "losing_days": sum(1 for day in trading_days if day["net_pnl"] < 0),
            "best_day": best_day,
            "worst_day": worst_day,
        },
        "daily_pnl": [{"date": day["date"], "pnl": day["net_pnl"]} for day in days],
        "days": days,
    }


def get_or_build_yearly_pnl(year: int) -> dict:
    report = store.get_yearly_report(year)
    if report is not None:
        return report
    prefix = f"{year}-"
    matching_dates = sorted(date for date in store.orders_by_date if date.startswith(prefix))
    if not matching_dates:
        raise HTTPException(status_code=404, detail=f"No data available for {year}.")
    source_order_count = sum(len(store.orders_by_date[trade_date]) for trade_date in matching_dates)
    cached = mongo_store.get_yearly_pnl(year)
    if (
        cached is not None
        and cached.get("source_date_count") == len(matching_dates)
        and cached.get("source_order_count") == source_order_count
        and isinstance(cached.get("report"), dict)
    ):
        report = cached["report"]
    else:
        logger.info("Building yearly pnl cache for %s", year)
        report = build_yearly_pnl_report(year)
        mongo_store.save_yearly_pnl(
            year=year,
            report=report,
            source_date_count=len(matching_dates),
            source_order_count=source_order_count,
        )
    store.set_yearly_report(year, report)
    return report


def get_or_build_daily_pnl_flow(date: str) -> dict | None:
    orders = store.get_orders(date)
    if orders is None:
        return None
    trades = pair_trades(orders)
    cached = mongo_store.get_daily_pnl_flow(date)
    if cached is not None and cached.get("source_trade_count") == len(trades) and isinstance(cached.get("report"), dict):
        return cached["report"]
    series, stats = pnl_flow(trades)
    report = {"date": date, "closed_pnl": series[-1]["cumulative_pnl"] if series else 0, "series": series, "stats": stats}
    mongo_store.save_daily_pnl_flow(date=date, report=report, source_trade_count=len(trades))
    return report


def get_monthly_journal(year: int, month: int) -> dict:
    prefix = f"{year}-{month:02d}-"
    matching_dates = sorted(date for date in store.orders_by_date if date.startswith(prefix))
    if not matching_dates:
        raise HTTPException(status_code=404, detail=f"No data available for {year}-{month:02d}.")
    days = []
    for date in matching_dates:
        orders = store.orders_by_date[date]
        trades = pair_trades(orders)
        stats = daily_stats(trades)
        best_instrument = None
        worst_instrument = None
        if trades:
            net_by_inst: dict[str, float] = {}
            for trade in trades:
                net_by_inst[trade["instrument"]] = net_by_inst.get(trade["instrument"], 0) + trade["pnl"]
            best_instrument = max(net_by_inst, key=net_by_inst.__getitem__)
            worst_instrument = min(net_by_inst, key=net_by_inst.__getitem__)
        days.append({
            "date": date,
            "net_pnl": stats["net_pnl"],
            "trades": stats["trades"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": stats["win_rate"],
            "best_trade_pnl": stats["best_trade_pnl"],
            "worst_trade_pnl": stats["worst_trade_pnl"],
            "max_drawdown": stats["max_drawdown"],
            "best_instrument": best_instrument,
            "worst_instrument": worst_instrument,
        })
    return {"year": year, "month": month, "summary": monthly_summary(days), "days": days}


def get_time_performance(date: str | None, year: int | None, month: int | None) -> dict:
    if not date and not (year and month):
        raise HTTPException(status_code=400, detail="Provide either ?date= or ?year=&month=")
    if date:
        orders = store.get_orders(date)
        if orders is None:
            raise HTTPException(status_code=404, detail=f"No orders available for {date}.")
        all_trades = pair_trades(orders)
        scope = "day"
    else:
        prefix = f"{year}-{month:02d}-"
        matching = [day for day in store.orders_by_date if day.startswith(prefix)]
        if not matching:
            raise HTTPException(status_code=404, detail=f"No data for {year}-{month:02d}.")
        all_trades = []
        for day in sorted(matching):
            all_trades.extend(pair_trades(store.orders_by_date[day]))
        scope = "month"
    windows = time_window_stats(all_trades)
    return {"scope": scope, "windows": windows, "pattern_insight": PatternInsightCrew(windows, scope).run()}


def build_daily_download_report(date: str) -> dict:
    daily_report = get_or_build_daily_report(date)
    if daily_report is None:
        raise HTTPException(status_code=404, detail=f"No daily report available for {date}.")
    orders = store.get_orders(date)
    if orders is None:
        raise HTTPException(status_code=404, detail=f"No orders available for {date}.")
    windows = time_window_stats(pair_trades(orders))
    instrument_reports = []
    seen_instruments: set[str] = set()
    for trade in daily_report.get("trades", []):
        instrument = trade.get("instrument")
        if not instrument or instrument in seen_instruments:
            continue
        seen_instruments.add(instrument)
        instrument_reports.append({
            "instrument": instrument,
            "report": get_or_build_instrument_report(date, instrument),
            "note": mongo_store.get_note(date=date, instrument=normalize_instrument(instrument)),
        })
    return {
        "report_type": "daily_trade_journal",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date,
        "daily_report": daily_report,
        "pnl_flow": get_or_build_daily_pnl_flow(date),
        "time_performance": {"scope": "day", "windows": windows, "pattern_insight": PatternInsightCrew(windows, "day").run()},
        "instrument_reports": instrument_reports,
    }
