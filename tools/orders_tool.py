"""
Orders tool: simulates fetching orders for a user on a given date.
For now, reads from the local sample orders.json (market was off today,
so we use the static file as a stand-in for the real API response).
"""

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent.parent / "data"


def fetch_orders(user_id: str, date: str) -> list[dict[str, Any]]:
    """
    Simulate an API call to fetch orders for a user on a given date.
    Returns the raw order list.

    In production this would be an authenticated HTTP call like:
        GET /api/v1/orders?user_id={user_id}&date={date}
    """
    # TODO: replace with real API call when market data is available
    orders_path = DATA_DIR / "orders.json"
    with open(orders_path) as f:
        return json.load(f)


def extract_instruments(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """
    From a list of orders, extract:
      - unique ref_ids from order_params
      - unique instruments as dicts ready for the Nubra SDK
        (keyed by symbol so duplicates are deduplicated)

    Returns:
        {
            "ref_ids": [900001, 900002, 900003],
            "instruments": [
                {"symbol": "INFY",     "exchange": "NSE", "type": "STOCK"},
                {"symbol": "RELIANCE", "exchange": "NSE", "type": "STOCK"},
                {"symbol": "TCS",      "exchange": "BSE", "type": "STOCK"},
            ]
        }
    """
    # keyed by symbol to deduplicate; last-seen exchange wins if there are dupes
    instruments: dict[str, dict[str, str]] = {}
    ref_ids: set[int] = set()

    for order in orders:
        params = order.get("order_params", {})

        symbol = params.get("asset") or order.get("display_name")
        exchange = order.get("exchange", "NSE")
        # order_params.asset_type is e.g. "STOCKS" — Nubra expects "STOCK"
        raw_type = params.get("asset_type", "STOCKS")
        nubra_type = raw_type.rstrip("S")  # "STOCKS" → "STOCK", "OPTIONS" → "OPTION"

        if symbol:
            instruments[symbol] = {"symbol": symbol, "exchange": exchange, "type": nubra_type}

        ref_id = params.get("ref_id")
        if ref_id:
            ref_ids.add(ref_id)

    return {
        "ref_ids": sorted(ref_ids),
        "instruments": sorted(instruments.values(), key=lambda x: x["symbol"]),
    }
