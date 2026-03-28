"""
Nubra SDK wrapper for fetching historical market data.

Docs: https://nubra.io/products/api/docs/python-sdk/market-data/historical-market-data.html

Key constraints from the SDK:
  - Max 5 instruments per request → batched automatically below
  - Rate limit: 60 calls/minute
  - Timestamps must be UTC ISO format
  - Prices returned in exchange-native units (paise for NSE)
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import islice
from typing import Any

from nubra_python_sdk.marketdata.market_data import MarketData
from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv


_BATCH_SIZE = 5  # SDK hard limit: max 5 symbols per call

DEFAULT_FIELDS = ["open", "high", "low", "close", "cumulative_volume"]


def _init_client() -> MarketData:
    """Initialise the Nubra SDK client (reads creds from environment)."""
    nubra = InitNubraSdk(NubraEnv.PROD, env_creds=True)
    return MarketData(nubra)


def _day_range_utc(date_str: str) -> tuple[str, str]:
    """
    Given an anchor date (YYYY-MM-DD), return a UTC range spanning the full
    calendar month so a single call covers all trading days in that month.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # first moment of the month
    start = dt.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # last moment of the month (use day 28 + roll to next month trick)
    if dt.month == 12:
        end_dt = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        end_dt = dt.replace(month=dt.month + 1, day=1)
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return start, end


def _explicit_range_utc(start_date: str, end_date: str) -> tuple[str, str]:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (
        start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    )


def _batched(iterable, n: int):
    """Yield successive n-sized chunks from iterable."""
    it = iter(iterable)
    while chunk := list(islice(it, n)):
        yield chunk


def fetch_historical_data(
    instruments: list[dict[str, str]],
    date: str,
    fields: list[str] = DEFAULT_FIELDS,
    interval: str = "1d",
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch historical OHLCV data for a list of instruments on a given date.

    Args:
        instruments: list of dicts, each with keys:
                     {"symbol": "RELIANCE", "exchange": "NSE", "type": "STOCK"}
        date:        trading date as "YYYY-MM-DD"
        fields:      Nubra field names to retrieve
        interval:    candle interval (default "1d" for end-of-day summary)

    Returns:
        Flat list of raw ChartData result dicts from the SDK, one per batch.
    """
    client = _init_client()
    if start_date and end_date:
        start_utc, end_utc = _explicit_range_utc(start_date, end_date)
    else:
        start_utc, end_utc = _day_range_utc(date)

    # Group instruments by (exchange, type) so each batch is homogeneous,
    # then respect the 5-symbol batch limit.
    from collections import defaultdict
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for inst in instruments:
        key = (inst["exchange"].upper(), inst["type"].upper())
        groups[key].append(inst["symbol"])

    all_results: list[dict[str, Any]] = []

    for (exchange, inst_type), symbols in groups.items():
        for batch in _batched(symbols, _BATCH_SIZE):
            response = client.historical_data({
                "exchange": exchange,
                "type": inst_type,
                "values": batch,
                "fields": fields,
                "startDate": start_utc,
                "endDate": end_utc,
                "interval": interval,
                "intraDay": False,
                "realTime": False,
            })
            all_results.append(response)

    return all_results
