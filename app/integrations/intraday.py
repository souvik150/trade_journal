"""
1-minute intraday OHLCV candles — store-first, Nubra SDK fallback.

Public API
----------
get_intraday_candles(symbol, exchange, inst_type, date)
    → list[dict]  — checks in-memory store first; fetches from Nubra on miss.

candles_to_csv(candles)
    → str  — compact CSV for LLM prompts.

compute_best_entry_exit(candles, direction, entry_time, exit_time)
    → dict  — deterministic best entry/exit from actual OHLCV, no AI involved.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _fetch_from_nubra(
    symbol: str,
    exchange: str,
    inst_type: str,
    date: str,            
) -> list[dict]:
    """
    Fetch 1m candles from Nubra PROD for the full trading session (09:15–15:30 IST).
    Returns parsed candle dicts. Returns [] on any failure.
    """
    try:
        from nubra_python_sdk.marketdata.market_data import MarketData
        from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

        start = f"{date}T03:45:00.000Z"
        end   = f"{date}T10:00:00.000Z"

        nubra  = InitNubraSdk(NubraEnv.PROD, env_creds=True)
        client = MarketData(nubra)

        response = client.historical_data({
            "exchange":  exchange,
            "type":      inst_type,
            "values":    [symbol],
            "fields":    ["open", "high", "low", "close", "cumulative_volume"],
            "startDate": start,
            "endDate":   end,
            "interval":  "1m",
            "intraDay":  False,
            "realTime":  False,
        })

        response_result: list = getattr(response, "result", None) or []
        if isinstance(response, dict) or not response_result:
            return []

        candles: list[dict] = []
        for chart_data in response_result:
            for stock_dict in chart_data.values:
                for _sym, sc in stock_dict.items():
                    if not sc.open:
                        continue
                    for i, pt in enumerate(sc.open):
                        ts = datetime.fromtimestamp(pt.timestamp / 1e9, tz=IST)
                        def _rs(val):
                            return round(val / 100.0, 2) if val is not None else None
                        candles.append({
                            "time":   ts.strftime("%H:%M"),
                            "open":   _rs(pt.value),
                            "high":   _rs(sc.high[i].value)              if sc.high              and i < len(sc.high)              else None,
                            "low":    _rs(sc.low[i].value)               if sc.low               and i < len(sc.low)               else None,
                            "close":  _rs(sc.close[i].value)             if sc.close             and i < len(sc.close)             else None,
                            "volume": sc.cumulative_volume[i].value      if sc.cumulative_volume and i < len(sc.cumulative_volume) else None,
                        })
        return candles

    except Exception as exc:
        logger.warning("Intraday fetch failed for %s on %s: %s", symbol, date, exc)
        return []


def get_intraday_candles(
    symbol: str,
    exchange: str,
    inst_type: str,
    date: str,
) -> list[dict]:
    """
    Return 1m OHLCV candles for (symbol, date).
    Checks in-memory store first; fetches from Nubra SDK on cache miss and stores result.
    """
    from app.core import store

    cached = store.get_intraday_candles(date, symbol)
    if cached is not None:
        logger.debug("Intraday candles for %s on %s served from store", symbol, date)
        return cached

    logger.info("Fetching intraday candles for %s on %s from Nubra", symbol, date)
    candles = _fetch_from_nubra(symbol, exchange, inst_type, date)
    store.set_intraday_candles(date, symbol, candles)
    return candles


def candles_to_csv(candles: list[dict]) -> str:
    """Convert candle dicts to a compact CSV string for LLM prompts."""
    if not candles:
        return ""
    lines = ["time,open,high,low,close,volume"]
    for c in candles:
        lines.append(
            f"{c['time']},"
            f"{c.get('open') or ''},"
            f"{c.get('high') or ''},"
            f"{c.get('low') or ''},"
            f"{c.get('close') or ''},"
            f"{c.get('volume') or ''}"
        )
    return "\n".join(lines)


def compute_best_entry_exit(
    candles: list[dict],
    direction: str,   # "LONG" or "SHORT"
    entry_time: str,  # HH:MM — actual entry
    exit_time: str,   # HH:MM — actual exit (unused, kept for future use)
) -> dict:
    """
    Deterministically compute the best possible entry and exit from OHLCV candles.

    LONG  — best entry: candle with min low  in [session_open → actual_entry]
             best exit:  candle with max high in [actual_entry → session_close]
    SHORT — best entry: candle with max high in [session_open → actual_entry]
             best exit:  candle with min low  in [actual_entry → session_close]

    Returns:
        {
          "best_entry": {"time": str, "price": float, "candle": dict} | None,
          "best_exit":  {"time": str, "price": float, "candle": dict} | None,
        }
    """
    if not candles:
        return {"best_entry": None, "best_exit": None}

    pre_entry  = [c for c in candles if c["time"] <= entry_time]
    post_entry = [c for c in candles if c["time"] >= entry_time]

    best_entry_candle: dict | None = None
    best_exit_candle:  dict | None = None

    if direction == "LONG":
        valid_pre  = [c for c in pre_entry  if c.get("low")  is not None]
        valid_post = [c for c in post_entry if c.get("high") is not None]
        if valid_pre:
            best_entry_candle = min(valid_pre,  key=lambda c: c["low"])
        if valid_post:
            best_exit_candle  = max(valid_post, key=lambda c: c["high"])
    else:  # SHORT
        valid_pre  = [c for c in pre_entry  if c.get("high") is not None]
        valid_post = [c for c in post_entry if c.get("low")  is not None]
        if valid_pre:
            best_entry_candle = max(valid_pre,  key=lambda c: c["high"])
        if valid_post:
            best_exit_candle  = min(valid_post, key=lambda c: c["low"])

    return {
        "best_entry": {
            "time":   best_entry_candle["time"],
            "price":  best_entry_candle["low"]  if direction == "LONG" else best_entry_candle["high"],
            "candle": best_entry_candle,
        } if best_entry_candle else None,
        "best_exit": {
            "time":   best_exit_candle["time"],
            "price":  best_exit_candle["high"] if direction == "LONG" else best_exit_candle["low"],
            "candle": best_exit_candle,
        } if best_exit_candle else None,
    }


def format_trades_for_llm(trades: list[dict]) -> str:
    """Compact readable representation of paired trades for LLM context."""
    if not trades:
        return "No trades recorded."
    lines = []
    for i, t in enumerate(trades, 1):
        sign = "+" if t["pnl"] >= 0 else ""
        lines.append(
            f"{i}. {t['instrument']} | {t['direction']} | "
            f"Entry {t['entry_time']} @ ₹{t['entry_price']} → "
            f"Exit {t['exit_time']} @ ₹{t['exit_price']} | "
            f"Qty {t['qty']} | P&L {sign}₹{t['pnl']}"
        )
    return "\n".join(lines)
