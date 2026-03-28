"""
Trade Journal AI — FastAPI entry point.

Endpoints
---------
POST /data/load                         manually rebuild in-memory data
GET  /journal/monthly?year=&month=      monthly calendar: per-day P&L / trades / WR + month summary
GET  /journal/daily?date=               cached daily trade log + smart insight + stats
GET  /journal/daily/export?date=        downloadable daily report CSV
GET  /journal/daily/report?date=        downloadable full-day report file
GET  /journal/yearly?year=              day-level P&L for all days in the year
GET  /journal/daily/pnl-flow?date=      intraday cumulative P&L time-series
GET  /analytics/time-performance?date=  P&L bucketed by time window (09:15-10:30 … 13:30-15:30)
GET  /trade?date=&instrument=           instrument-level detail for one day
GET  /notes?date=&instrument=           notes for one instrument/day
POST /notes/text                        add text note for one instrument/day
POST /notes/voice                       transcribe and save voice note for one instrument/day
"""

import json
import io
import csv
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

import mongo_store
import store
from tools.orders_tool import extract_instruments
from tools.nubra_tool import fetch_historical_data
from tools.analytics import pair_trades, daily_stats, monthly_summary, pnl_flow, time_window_stats
from tools.intraday_tool import fetch_intraday_csv
from crews.trade_journal_crew import DailyJournalCrew, InstrumentAnalysisCrew, PatternInsightCrew, TradeTaggingCrew

logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_app):
    _bootstrap_data()
    logger.info("Trade Journal AI running on http://0.0.0.0:8000")
    yield

app = FastAPI(title="Trade Journal AI", version="0.2.0", lifespan=lifespan)

DATA_DIR = Path(__file__).parent / "data"


@app.middleware("http")
async def capture_request_metrics(request: Request, call_next):
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        mongo_store.log_api_request(
            method=request.method,
            path=request.url.path,
            query=str(request.query_params),
            status_code=500,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            error=str(exc),
        )
        raise

    mongo_store.log_api_request(
        method=request.method,
        path=request.url.path,
        query=str(request.query_params),
        status_code=response.status_code,
        duration_ms=(time.perf_counter() - started_at) * 1000,
    )
    return response


def _normalize_instrument(instrument: str) -> str:
    return instrument.strip().upper()


def _is_stock_order(order: dict) -> bool:
    params = order.get("order_params", {})
    return params.get("asset_type") == "STOCKS"


def _trade_tag_enrichment(trade: dict, trade_date: str) -> dict:
    return TradeTaggingCrew(trade, trade_date).run()


def _build_public_trade(trade: dict, trade_no: int, trade_date: str) -> dict:
    tag_result = _trade_tag_enrichment(trade, trade_date)
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


def _build_daily_report(date: str, orders: list[dict]) -> dict:
    trades = pair_trades(orders)
    stats = daily_stats(trades)
    public_trades = [
        _build_public_trade(trade, i, date)
        for i, trade in enumerate(trades, start=1)
    ]
    return {
        "date": date,
        "stats": {
            "net_pnl":        stats["net_pnl"],
            "trades":         stats["trades"],
            "wins":           stats["wins"],
            "losses":         stats["losses"],
            "win_rate":       stats["win_rate"],
            "best_trade_pnl": stats["best_trade_pnl"],
            "max_drawdown":   stats["max_drawdown"],
        },
        "smart_insight": DailyJournalCrew(trades, date, stats).run(),
        "trades": public_trades,
    }


def _find_instrument_trades(date: str, instrument: str) -> tuple[list[dict], list[dict], dict]:
    orders = store.get_orders(date)
    if orders is None:
        raise HTTPException(status_code=404, detail=f"No orders available for {date}.")

    daily_report = _get_or_build_daily_report(date)
    if daily_report is None:
        raise HTTPException(status_code=404, detail=f"No daily report available for {date}.")

    raw_trades = pair_trades(orders)
    normalized_instrument = instrument.strip().upper()
    matching_raw_trades = [trade for trade in raw_trades if trade["instrument"].upper() == normalized_instrument]
    matching_public_trades = [
        trade for trade in daily_report["trades"]
        if trade["instrument"].upper() == normalized_instrument
    ]

    if not matching_raw_trades or not matching_public_trades:
        raise HTTPException(
            status_code=404,
            detail=f"No trades found for instrument {instrument} on {date}.",
        )

    instrument_meta = matching_raw_trades[0]
    return matching_raw_trades, matching_public_trades, instrument_meta


def _ensure_instrument_exists(date: str, instrument: str) -> dict:
    orders = store.get_orders(date)
    if orders is None:
        raise HTTPException(status_code=404, detail=f"No orders available for {date}.")

    normalized_instrument = _normalize_instrument(instrument)
    matching_orders = [
        order for order in orders
        if str(order.get("display_name", "")).upper() == normalized_instrument
    ]
    if not matching_orders:
        raise HTTPException(
            status_code=404,
            detail=f"No orders found for instrument {instrument} on {date}.",
        )

    return matching_orders[0]


def _instrument_cache_key(date: str, instrument: str) -> str:
    return f"{date}::{_normalize_instrument(instrument)}"


def _transcribe_voice_note(audio_file: UploadFile) -> tuple[str, str]:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is required for voice transcription.")

    from openai import OpenAI

    model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    client = OpenAI()
    started_at = time.perf_counter()
    audio_file.file.seek(0)
    content = audio_file.file.read()
    named_buffer = io.BytesIO(content)
    named_buffer.name = audio_file.filename or "voice_note.wav"
    try:
        transcript = client.audio.transcriptions.create(
            model=model,
            file=named_buffer,
        )
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
    text = getattr(transcript, "text", None) or ""
    text = text.strip()
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI returned an empty transcription.")
    return text, model


def _build_instrument_report(date: str, instrument: str) -> dict:
    raw_trades, public_trades, instrument_meta = _find_instrument_trades(date, instrument)
    stats = daily_stats(raw_trades)
    md = store.get_md(instrument_meta["asset"])
    candles = md["candles"] if md else []
    intraday_csv = fetch_intraday_csv(
        symbol=instrument_meta["asset"],
        exchange=md["exchange"] if md else "NSE",
        inst_type=instrument_meta["asset_type"].rstrip("S"),
        date=date,
    )
    ai_result = InstrumentAnalysisCrew(
        instrument=instrument_meta["instrument"],
        date=date,
        trades=raw_trades,
        intraday_csv=intraday_csv,
    ).run()
    first_trade = public_trades[0]
    last_trade = public_trades[-1]

    return {
        "date": date,
        "instrument": instrument_meta["instrument"],
        "asset": instrument_meta["asset"],
        "asset_type": instrument_meta["asset_type"],
        "derivative_type": instrument_meta["derivative_type"],
        "direction": first_trade["direction"],
        "entry": {"time": first_trade["entry_time"], "price": first_trade["entry_price"]},
        "exit": {"time": last_trade["exit_time"], "price": last_trade["exit_price"]},
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
        "ohlcv_candles": candles,
        "signal_timeline": ai_result["signal_timeline"],
        "analysis": ai_result["analysis"],
        "trades": public_trades,
    }


def _get_or_build_instrument_report(date: str, instrument: str) -> dict:
    cache_key = _instrument_cache_key(date, instrument)
    report = store.get_instrument_report(cache_key)
    if report is not None:
        return report

    raw_trades, _, _ = _find_instrument_trades(date, instrument)
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
        report = _build_instrument_report(date, instrument)
        mongo_store.save_instrument_report(
            cache_key=cache_key,
            date=date,
            instrument=instrument.strip().upper(),
            report=report,
            source_trade_count=len(raw_trades),
            ai_generated=bool(os.getenv("OPENAI_API_KEY")),
        )

    store.set_instrument_report(cache_key, report)
    return report


def _load_orders_into_store() -> dict[str, dict]:
    store.clear_orders()
    all_instruments: dict[str, dict] = {}
    orders_by_date = mongo_store.get_all_orders_by_date()
    if not orders_by_date:
        raise HTTPException(status_code=404, detail="No orders found in MongoDB. Run the Mongo import script first.")

    for date, orders in sorted(orders_by_date.items()):
        stock_orders = [order for order in orders if _is_stock_order(order)]
        store.set_orders(date, stock_orders)

        extracted = extract_instruments(stock_orders)
        for inst in extracted["instruments"]:
            all_instruments[inst["symbol"]] = inst

    logger.info(
        "Loaded %d dates and %d stock instruments into memory",
        len(store.orders_by_date),
        len(all_instruments),
    )
    return all_instruments


def _load_market_data_into_store(instruments: list[dict]) -> None:
    store.clear_md()

    has_creds = os.getenv("PHONE_NO") and os.getenv("MPIN")
    has_session = any(Path(__file__).parent.glob("auth_data.db*"))
    if not has_creds and not has_session:
        logger.warning(
            "No Nubra credentials (PHONE_NO/MPIN) and no cached session — skipping OHLCV fetch. Run `make login`."
        )
        return

    try:
        order_dates = sorted(store.orders_by_date.keys())
        start_date = order_dates[0]
        end_date = order_dates[-1]
        results = fetch_historical_data(
            instruments,
            date=start_date,
            interval="1d",
            start_date=start_date,
            end_date=end_date,
        )
        logger.info("Nubra OHLCV fetch OK — %d result batches", len(results))
    except Exception as exc:
        logger.warning("Nubra fetch failed (OHLCV unavailable): %s", exc)
        return

    for response in results:
        if not response or isinstance(response, dict) or not response.result:
            continue
        for chart_data in response.result:
            exchange = chart_data.exchange
            inst_type = chart_data.type
            for stock_dict in chart_data.values:
                for symbol, sc in stock_dict.items():
                    opens = sc.open or []
                    highs = sc.high or []
                    lows = sc.low or []
                    closes = sc.close or []
                    volumes = sc.cumulative_volume or []

                    candles = [
                        {
                            "timestamp_ns": opens[i].timestamp,
                            "open": opens[i].value,
                            "high": highs[i].value if i < len(highs) else None,
                            "low": lows[i].value if i < len(lows) else None,
                            "close": closes[i].value if i < len(closes) else None,
                            "volume": volumes[i].value if i < len(volumes) else None,
                        }
                        for i in range(len(opens))
                    ]
                    store.set_md(symbol, exchange, inst_type, candles)


def _get_or_build_daily_report(date: str) -> dict | None:
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
        report = _build_daily_report(date, orders)
        mongo_store.save_daily_report(
            date=date,
            report=report,
            source_order_count=len(orders),
            ai_generated=bool(os.getenv("OPENAI_API_KEY")),
        )

    store.set_daily_report(date, report)
    return report


def _build_yearly_pnl_report(year: int) -> dict:
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
    winning_days = sum(1 for day in trading_days if day["net_pnl"] > 0)
    losing_days = sum(1 for day in trading_days if day["net_pnl"] < 0)
    best_day = max(trading_days, key=lambda day: day["net_pnl"]) if trading_days else None
    worst_day = min(trading_days, key=lambda day: day["net_pnl"]) if trading_days else None

    return {
        "year": year,
        "summary": {
            "net_pnl": round(sum(day["net_pnl"] for day in days), 2),
            "trading_days": len(trading_days),
            "total_trades": total_trades,
            "winning_days": winning_days,
            "losing_days": losing_days,
            "best_day": best_day,
            "worst_day": worst_day,
        },
        "daily_pnl": [
            {"date": day["date"], "pnl": day["net_pnl"]}
            for day in days
        ],
        "days": days,
    }


def _get_or_build_yearly_pnl(year: int) -> dict:
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
        report = _build_yearly_pnl_report(year)
        mongo_store.save_yearly_pnl(
            year=year,
            report=report,
            source_date_count=len(matching_dates),
            source_order_count=source_order_count,
        )

    store.set_yearly_report(year, report)
    return report


def _get_or_build_daily_pnl_flow(date: str) -> dict | None:
    orders = store.get_orders(date)
    if orders is None:
        return None

    trades = pair_trades(orders)
    cached = mongo_store.get_daily_pnl_flow(date)
    if (
        cached is not None
        and cached.get("source_trade_count") == len(trades)
        and isinstance(cached.get("report"), dict)
    ):
        return cached["report"]

    series, stats = pnl_flow(trades)
    report = {
        "date": date,
        "closed_pnl": series[-1]["cumulative_pnl"] if series else 0,
        "series": series,
        "stats": stats,
    }
    mongo_store.save_daily_pnl_flow(
        date=date,
        report=report,
        source_trade_count=len(trades),
    )
    return report


def _build_daily_download_report(date: str) -> dict:
    daily_report = _get_or_build_daily_report(date)
    if daily_report is None:
        raise HTTPException(status_code=404, detail=f"No daily report available for {date}.")
    orders = store.get_orders(date)
    if orders is None:
        raise HTTPException(status_code=404, detail=f"No orders available for {date}.")
    all_trades = pair_trades(orders)
    windows = time_window_stats(all_trades)

    instrument_names = []
    seen_instruments = set()
    for trade in daily_report.get("trades", []):
        instrument = trade.get("instrument")
        if not instrument or instrument in seen_instruments:
            continue
        seen_instruments.add(instrument)
        instrument_names.append(instrument)

    instrument_reports = []
    for instrument in instrument_names:
        instrument_reports.append({
            "instrument": instrument,
            "report": _get_or_build_instrument_report(date, instrument),
            "note": mongo_store.get_note(date=date, instrument=_normalize_instrument(instrument)),
        })

    return {
        "report_type": "daily_trade_journal",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date,
        "daily_report": daily_report,
        "pnl_flow": _get_or_build_daily_pnl_flow(date),
        "time_performance": {
            "scope": "day",
            "windows": windows,
            "pattern_insight": PatternInsightCrew(windows, "day").run(),
        },
        "instrument_reports": instrument_reports,
    }


def _pdf_write_line(text_object, text: str = "") -> None:
    text_object.textLine((text or "")[:2000])


def _pdf_write_wrapped(text_object, text: str, width: int = 105) -> None:
    if not text:
        _pdf_write_line(text_object, "-")
        return

    words = str(text).split()
    if not words:
        _pdf_write_line(text_object, "-")
        return

    line = words[0]
    for word in words[1:]:
        candidate = f"{line} {word}"
        if len(candidate) <= width:
            line = candidate
        else:
            _pdf_write_line(text_object, line)
            line = word
    _pdf_write_line(text_object, line)


def _start_pdf_page(pdf, title: str):
    from reportlab.lib.pagesizes import A4

    width, height = A4
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(40, height - 40, title)
    pdf.setFont("Helvetica", 10)
    text_object = pdf.beginText(40, height - 65)
    text_object.setLeading(14)
    return text_object, width, height


def _finish_pdf_page(pdf, text_object) -> None:
    pdf.drawText(text_object)
    pdf.showPage()


def _render_daily_report_pdf(report: dict) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PDF export requires reportlab to be installed.") from exc

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    date = report["date"]

    text, _, _ = _start_pdf_page(pdf, f"Trade Journal Report - {date}")
    daily = report["daily_report"]
    stats = daily["stats"]
    _pdf_write_line(text, f"Generated at: {report['generated_at']}")
    _pdf_write_line(text)
    _pdf_write_line(text, "Day Summary")
    _pdf_write_line(text, f"Net P&L: {stats['net_pnl']}")
    _pdf_write_line(text, f"Trades: {stats['trades']} | Wins: {stats['wins']} | Losses: {stats['losses']} | Win Rate: {stats['win_rate']}")
    _pdf_write_line(text, f"Best Trade P&L: {stats['best_trade_pnl']} | Max Drawdown: {stats['max_drawdown']}")
    _pdf_write_line(text)
    _pdf_write_line(text, "Smart Insights")
    for insight in daily.get("smart_insight", []):
        _pdf_write_wrapped(text, f"- {insight}")
    _pdf_write_line(text)
    _pdf_write_line(text, "Time Performance")
    for window in report["time_performance"]["windows"]:
        _pdf_write_line(
            text,
            f"{window['label']}: pnl={window['pnl']} trades={window['trades']} wins={window['wins']} losses={window['losses']} tag={window.get('tag')}",
        )
    pattern_insight = report["time_performance"].get("pattern_insight")
    if pattern_insight:
        _pdf_write_line(text)
        _pdf_write_wrapped(text, f"Pattern Insight: {pattern_insight}")
    _finish_pdf_page(pdf, text)

    for instrument_item in report["instrument_reports"]:
        instrument_report = instrument_item["report"]
        note = instrument_item.get("note")
        text, _, _ = _start_pdf_page(pdf, f"{instrument_report['instrument']} - {date}")
        instrument_stats = instrument_report["stats"]
        _pdf_write_line(text, f"Direction: {instrument_report.get('direction')}")
        _pdf_write_line(
            text,
            f"Entry: {instrument_report['entry']['time']} @ {instrument_report['entry']['price']} | Exit: {instrument_report['exit']['time']} @ {instrument_report['exit']['price']}",
        )
        _pdf_write_line(text, f"Qty: {instrument_report['qty']} | Net P&L: {instrument_report['pnl']}")
        _pdf_write_line(
            text,
            f"Trades: {instrument_stats['trades']} | Wins: {instrument_stats['wins']} | Losses: {instrument_stats['losses']} | Win Rate: {instrument_stats['win_rate']}",
        )
        if instrument_report.get("strategy") is not None or instrument_report.get("emotion") is not None:
            _pdf_write_line(
                text,
                f"Strategy: {instrument_report.get('strategy')} | Emotion: {instrument_report.get('emotion')}",
            )
        _pdf_write_line(text)
        _pdf_write_line(text, "Signal Timeline")
        for event in instrument_report.get("signal_timeline", []):
            _pdf_write_line(text, f"{event.get('time')} | {event.get('tag')} | {event.get('title')}")
            _pdf_write_wrapped(text, event.get("description", ""))
        _pdf_write_line(text)
        _pdf_write_line(text, "Analysis")
        analysis = instrument_report.get("analysis", {})
        for key in ["why_it_worked", "what_could_be_better", "post_exit_summary", "trade_narrative"]:
            if analysis.get(key):
                label = key.replace("_", " ").title()
                _pdf_write_wrapped(text, f"{label}: {analysis[key]}")
        lessons = analysis.get("lessons") or []
        if lessons:
            _pdf_write_line(text, "Lessons")
            for lesson in lessons:
                _pdf_write_wrapped(text, f"- {lesson}")
        _pdf_write_line(text)
        _pdf_write_line(text, "Trades")
        for trade in instrument_report.get("trades", []):
            _pdf_write_line(
                text,
                f"#{trade['trade_no']} {trade['direction']} qty={trade['qty']} entry={trade['entry_time']}@{trade['entry_price']} exit={trade['exit_time']}@{trade['exit_price']} pnl={trade['pnl']}",
            )
        _pdf_write_line(text)
        _pdf_write_line(text, "Note")
        if note:
            _pdf_write_wrapped(text, note.get("text", ""))
        else:
            _pdf_write_line(text, "No note saved.")
        _finish_pdf_page(pdf, text)

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def _render_daily_report_csv(daily_report: dict) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "date",
            "net_pnl",
            "trades_count",
            "wins",
            "losses",
            "win_rate",
            "best_trade_pnl",
            "max_drawdown",
            "trade_no",
            "instrument",
            "derivative_type",
            "direction",
            "entry_time",
            "entry_price",
            "exit_time",
            "exit_price",
            "qty",
            "pnl",
            "strategy",
        ],
    )
    writer.writeheader()

    stats = daily_report["stats"]
    base_row = {
        "date": daily_report["date"],
        "net_pnl": stats["net_pnl"],
        "trades_count": stats["trades"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": stats["win_rate"],
        "best_trade_pnl": stats["best_trade_pnl"],
        "max_drawdown": stats["max_drawdown"],
    }

    trades = daily_report.get("trades", [])
    if not trades:
        writer.writerow(base_row)
        return output.getvalue()

    for trade in trades:
        writer.writerow({
            **base_row,
            "trade_no": trade.get("trade_no"),
            "instrument": trade.get("instrument"),
            "derivative_type": trade.get("derivative_type"),
            "direction": trade.get("direction"),
            "entry_time": trade.get("entry_time"),
            "entry_price": trade.get("entry_price"),
            "exit_time": trade.get("exit_time"),
            "exit_price": trade.get("exit_price"),
            "qty": trade.get("qty"),
            "pnl": trade.get("pnl"),
            "strategy": trade.get("strategy"),
        })

    return output.getvalue()


def _bootstrap_data() -> None:
    all_instruments = _load_orders_into_store()
    _load_market_data_into_store(list(all_instruments.values()))
    store.clear_daily_reports()
    store.clear_instrument_reports()
    store.clear_yearly_reports()

@app.post("/data/load")
def load_data():
    """
    Manual rebuild hook. Normal API usage does not require this endpoint;
    startup already loads orders, and daily reports are generated lazily on first request.
    """
    _bootstrap_data()

    return {
        "status":            "rebuilt",
        "dates_loaded":      len(store.orders_by_date),
        "reports_cached":    len(store.daily_reports_by_date),
        "symbols_with_ohlcv": store.all_symbols(),
        "total_orders":      sum(len(v) for v in store.orders_by_date.values()),
    }

@app.get("/journal/monthly")
def journal_monthly(
    year:  int = Query(...),
    month: int = Query(...),
):
    """
    Returns per-day P&L, trade count, win-rate, and
    month-level summary stats.
    """
    prefix = f"{year}-{month:02d}-"
    matching_dates = sorted(
        date for date in store.orders_by_date if date.startswith(prefix)
    )

    if not matching_dates:
        raise HTTPException(
            status_code=404,
            detail=f"No data available for {year}-{month:02d}.",
        )

    days = []
    for date in matching_dates:
        orders = store.orders_by_date[date]
        trades = pair_trades(orders)
        stats  = daily_stats(trades)

        best_instrument  = None
        worst_instrument = None
        if trades:
            net_by_inst: dict[str, float] = {}
            for t in trades:
                net_by_inst[t["instrument"]] = net_by_inst.get(t["instrument"], 0) + t["pnl"]
            best_instrument  = max(net_by_inst, key=net_by_inst.__getitem__)
            worst_instrument = min(net_by_inst, key=net_by_inst.__getitem__)

        days.append({
            "date":             date,
            "net_pnl":          stats["net_pnl"],
            "trades":           stats["trades"],
            "wins":             stats["wins"],
            "losses":           stats["losses"],
            "win_rate":         stats["win_rate"],
            "best_trade_pnl":   stats["best_trade_pnl"],
            "worst_trade_pnl":  stats["worst_trade_pnl"],
            "max_drawdown":     stats["max_drawdown"],
            "best_instrument":  best_instrument,
            "worst_instrument": worst_instrument,
        })

    summary = monthly_summary(days)

    return {
        "year":    year,
        "month":   month,
        "summary": summary,
        "days":    days,
    }

@app.get("/journal/daily")
def journal_daily(date: str = Query(...)):
    """
    Returns the cached daily trade log for a single day, generating it on first request.
    """
    report = _get_or_build_daily_report(date)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"No daily report available for {date}.",
        )
    return report


@app.get("/journal/daily/export")
def journal_daily_export(date: str = Query(...)):
    """
    Returns a downloadable CSV file for the daily journal payload.
    Includes one row per trade, or one summary row if the day has no trades.
    """
    report = _get_or_build_daily_report(date)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"No daily report available for {date}.",
        )

    csv_text = _render_daily_report_csv(report)
    filename = f"trade_journal_daily_{date}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/journal/daily/report")
def journal_daily_report_download(date: str = Query(...)):
    """
    Returns a downloadable PDF file containing the daily report plus
    all instrument-level reports for the date.
    """
    report = _build_daily_download_report(date)
    pdf_bytes = _render_daily_report_pdf(report)
    filename = f"trade_journal_report_{date}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/journal/yearly")
def journal_yearly(year: int = Query(...)):
    """
    Returns day-level P&L rows for every available trading day in the given year.
    """
    return _get_or_build_yearly_pnl(year)

@app.get("/journal/daily/pnl-flow")
def journal_pnl_flow(date: str = Query(...)):
    """
    Per-minute cumulative realised P&L across the trading session
    for all instruments on the day.
    """
    report = _get_or_build_daily_pnl_flow(date)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No orders available for {date}.")
    return report

@app.get("/analytics/time-performance")
def analytics_time_performance(
    date:  str | None = Query(None),
    year:  int | None = Query(None),
    month: int | None = Query(None),
):
    """
    P&L bucketed by session time window.
    Pass ?date= for a single day, or ?year=&month= for a monthly roll-up
    (all trading days in that month are aggregated together).
    pattern_insight is null until CrewAI is wired up.
    """
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
        matching = [d for d in store.orders_by_date if d.startswith(prefix)]
        if not matching:
            raise HTTPException(status_code=404, detail=f"No data for {year}-{month:02d}.")
        all_trades = []
        for d in sorted(matching):
            all_trades.extend(pair_trades(store.orders_by_date[d]))
        scope = "month"

    windows = time_window_stats(all_trades)

    return {
        "scope":           scope,
        "windows":         windows,
        "pattern_insight": PatternInsightCrew(windows, scope).run(),
    }

@app.get("/trade")
def trade_detail_by_instrument(
    date: str = Query(...),
    instrument: str = Query(...),
):
    """
    Instrument-level detail for a single trading day.
    Returns all paired trades for that instrument on the date,
    plus per-instrument stats and daily OHLCV candles.
    """
    return _get_or_build_instrument_report(date, instrument)


@app.get("/notes")
def get_notes(
    date: str = Query(...),
    instrument: str = Query(...),
):
    _ensure_instrument_exists(date, instrument)
    normalized_instrument = _normalize_instrument(instrument)
    return {
        "date": date,
        "instrument": normalized_instrument,
        "note": mongo_store.get_note(date=date, instrument=normalized_instrument),
    }


@app.post("/notes/text")
def create_text_note(
    date: str = Form(...),
    instrument: str = Form(...),
    text: str = Form(...),
):
    _ensure_instrument_exists(date, instrument)
    normalized_instrument = _normalize_instrument(instrument)
    cleaned_text = text.strip()
    if not cleaned_text:
        raise HTTPException(status_code=400, detail="Note text cannot be empty.")

    note = mongo_store.create_note(
        date=date,
        instrument=normalized_instrument,
        text=cleaned_text,
        source="text",
    )
    if note is None:
        raise HTTPException(status_code=503, detail="MongoDB is not available.")

    return {
        "status": "saved",
        "note": note,
    }


@app.post("/notes/voice")
def create_voice_note(
    date: str = Form(...),
    instrument: str = Form(...),
    file: UploadFile = File(...),
):
    _ensure_instrument_exists(date, instrument)
    normalized_instrument = _normalize_instrument(instrument)
    transcript_text, model = _transcribe_voice_note(file)

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

    return {
        "status": "saved",
        "note": note,
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "dates_loaded": len(store.orders_by_date),
        "reports_cached": len(store.daily_reports_by_date),
        "yearly_cached": len(store.yearly_reports_by_year),
        "mongo_connected": mongo_store.is_available(),
        "symbols_with_ohlcv": len(store.md_data),
    }


@app.get("/monitoring/summary")
def monitoring_summary(window_minutes: int = Query(60, ge=1, le=1440)):
    return mongo_store.get_monitoring_summary(window_minutes=window_minutes)


@app.get("/monitoring/alerts")
def monitoring_alerts(window_minutes: int = Query(60, ge=1, le=1440)):
    summary = mongo_store.get_monitoring_summary(window_minutes=window_minutes)
    return {
        "window_minutes": window_minutes,
        "alerts": summary.get("alerts", []),
    }


@app.get("/monitoring", response_class=HTMLResponse)
def monitoring_dashboard(window_minutes: int = Query(60, ge=1, le=1440)):
    summary = mongo_store.get_monitoring_summary(window_minutes=window_minutes)
    alerts_html = "".join(
        f"<li><strong>{alert['severity'].upper()}</strong>: {alert['message']}</li>"
        for alert in summary.get("alerts", [])
    ) or "<li>No active alerts.</li>"

    routes_html = "".join(
        (
            "<tr>"
            f"<td>{route['path']}</td>"
            f"<td>{route['requests']}</td>"
            f"<td>{route['failure_count']}</td>"
            f"<td>{route['avg_latency_ms']}</td>"
            f"<td>{route['max_latency_ms']}</td>"
            f"<td>{route['last_status_code']}</td>"
            "</tr>"
        )
        for route in summary.get("api", {}).get("routes", [])
    ) or "<tr><td colspan='6'>No API traffic in this window.</td></tr>"

    llm_html = "".join(
        (
            "<tr>"
            f"<td>{op['operation']}</td>"
            f"<td>{op['calls']}</td>"
            f"<td>{op['failure_count']}</td>"
            f"<td>{op['avg_latency_ms']}</td>"
            f"<td>{op['model']}</td>"
            "</tr>"
        )
        for op in summary.get("llm", {}).get("operations", [])
    ) or "<tr><td colspan='5'>No LLM calls in this window.</td></tr>"

    return f"""
    <html>
      <head>
        <title>Trade Journal Monitoring</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 32px; background: #f5f7fb; color: #162033; }}
          .cards {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
          .card {{ background: white; border-radius: 12px; padding: 16px 20px; min-width: 220px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); }}
          table {{ width: 100%; border-collapse: collapse; background: white; margin-bottom: 24px; }}
          th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e6ebf2; }}
          h1, h2 {{ margin-bottom: 12px; }}
          ul {{ background: white; padding: 16px 24px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); }}
        </style>
      </head>
      <body>
        <h1>Trade Journal Monitoring</h1>
        <p>Window: last {window_minutes} minute(s)</p>
        <div class="cards">
          <div class="card"><strong>Mongo</strong><br>{summary.get('mongo_connected')}</div>
          <div class="card"><strong>API Requests</strong><br>{summary.get('api', {}).get('total_requests')}</div>
          <div class="card"><strong>API Avg Latency</strong><br>{summary.get('api', {}).get('avg_latency_ms')} ms</div>
          <div class="card"><strong>LLM Calls</strong><br>{summary.get('llm', {}).get('total_calls')}</div>
          <div class="card"><strong>LLM Avg Latency</strong><br>{summary.get('llm', {}).get('avg_latency_ms')} ms</div>
        </div>
        <h2>Alerts</h2>
        <ul>{alerts_html}</ul>
        <h2>API Routes</h2>
        <table>
          <tr><th>Path</th><th>Requests</th><th>Failures</th><th>Avg ms</th><th>Max ms</th><th>Last Status</th></tr>
          {routes_html}
        </table>
        <h2>LLM Workflows</h2>
        <table>
          <tr><th>Operation</th><th>Calls</th><th>Failures</th><th>Avg ms</th><th>Model</th></tr>
          {llm_html}
        </table>
      </body>
    </html>
    """


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        log_level="info",
        access_log=True,
    )
