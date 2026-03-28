"""
In-memory store — single source of truth for the lifetime of the process.

Sections
--------
orders_by_date        : { "2025-03-26": [ <order>, ... ] }
md_data               : { "RELIANCE": { "exchange": "NSE", "type": "STOCK",
                                        "candles": [ {timestamp, open, high, low, close, volume}, ... ] } }
daily_reports_by_date : { "2025-03-26": <daily report dict> }
instrument_reports    : { "2025-03-26::AXISBANK": <instrument detail dict> }
yearly_reports_by_year: { 2025: <yearly pnl dict> }
"""

from typing import Any

# ── orders loaded from data/ files ──────────────────────────────────────────
orders_by_date: dict[str, list[dict]] = {}

# ── OHLCV candles fetched from Nubra, keyed by symbol ───────────────────────
md_data: dict[str, dict[str, Any]] = {}

# ── precomputed daily journal payloads keyed by date ────────────────────────
daily_reports_by_date: dict[str, dict[str, Any]] = {}

# ── instrument/day detail payloads keyed by date::instrument ────────────────
instrument_reports: dict[str, dict[str, Any]] = {}

# ── yearly pnl payloads keyed by year ────────────────────────────────────────
yearly_reports_by_year: dict[int, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# orders helpers
# ---------------------------------------------------------------------------

def set_orders(date: str, orders: list[dict]) -> None:
    orders_by_date[date] = orders


def get_orders(date: str) -> list[dict] | None:
    return orders_by_date.get(date)


def all_orders_flat() -> list[dict]:
    """All orders across every loaded date, with a 'date' key injected."""
    result = []
    for date, orders in orders_by_date.items():
        for o in orders:
            result.append({**o, "_date": date})
    return result


# ---------------------------------------------------------------------------
# md_data helpers
# ---------------------------------------------------------------------------

def set_md(symbol: str, exchange: str, inst_type: str, candles: list[dict]) -> None:
    md_data[symbol] = {
        "exchange":  exchange,
        "type":      inst_type,
        "candles":   candles,   # [{timestamp_ns, open, high, low, close, volume}]
    }


def get_md(symbol: str) -> dict | None:
    return md_data.get(symbol)


def all_symbols() -> list[str]:
    return list(md_data.keys())


# ---------------------------------------------------------------------------
# daily report helpers
# ---------------------------------------------------------------------------

def set_daily_report(date: str, report: dict[str, Any]) -> None:
    daily_reports_by_date[date] = report


def get_daily_report(date: str) -> dict[str, Any] | None:
    return daily_reports_by_date.get(date)


def all_daily_report_dates() -> list[str]:
    return sorted(daily_reports_by_date.keys())


def clear_orders() -> None:
    orders_by_date.clear()


def clear_md() -> None:
    md_data.clear()


def clear_daily_reports() -> None:
    daily_reports_by_date.clear()


def set_instrument_report(cache_key: str, report: dict[str, Any]) -> None:
    instrument_reports[cache_key] = report


def get_instrument_report(cache_key: str) -> dict[str, Any] | None:
    return instrument_reports.get(cache_key)


def clear_instrument_reports() -> None:
    instrument_reports.clear()


def set_yearly_report(year: int, report: dict[str, Any]) -> None:
    yearly_reports_by_year[year] = report


def get_yearly_report(year: int) -> dict[str, Any] | None:
    return yearly_reports_by_year.get(year)


def clear_yearly_reports() -> None:
    yearly_reports_by_year.clear()
