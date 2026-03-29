from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import HTTPException

from app.core import store
from app.db import mongo_store
from app.integrations.nubra import fetch_historical_data


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_stock_order(order: dict) -> bool:
    params = order.get("order_params", {})
    return params.get("asset_type") == "STOCKS"


def extract_runtime_instruments(orders: list[dict]) -> list[dict[str, str]]:
    instruments: dict[str, dict[str, str]] = {}
    for order in orders:
        params = order.get("order_params", {})
        symbol = params.get("asset") or order.get("display_name")
        exchange = order.get("exchange", "NSE")
        raw_type = params.get("asset_type", "STOCKS")
        nubra_type = raw_type.rstrip("S")
        if symbol:
            instruments[symbol] = {"symbol": symbol, "exchange": exchange, "type": nubra_type}
    return sorted(instruments.values(), key=lambda item: item["symbol"])


def load_orders_into_store() -> dict[str, dict]:
    store.clear_orders()
    all_instruments: dict[str, dict] = {}
    orders_by_date = mongo_store.get_all_orders_by_date()
    if not orders_by_date:
        raise HTTPException(status_code=404, detail="No orders found in MongoDB. Run the Mongo import script first.")

    for date, orders in sorted(orders_by_date.items()):
        stock_orders = [order for order in orders if is_stock_order(order)]
        store.set_orders(date, stock_orders)
        for inst in extract_runtime_instruments(stock_orders):
            all_instruments[inst["symbol"]] = inst

    logger.info(
        "Loaded %d dates and %d stock instruments into memory",
        len(store.orders_by_date),
        len(all_instruments),
    )
    return all_instruments


def load_market_data_into_store(instruments: list[dict]) -> None:
    store.clear_md()
    has_creds = os.getenv("PHONE_NO") and os.getenv("MPIN")
    has_session = any(PROJECT_ROOT.glob("auth_data.db*"))
    if not has_creds and not has_session:
        logger.warning("No Nubra credentials (PHONE_NO/MPIN) and no cached session — skipping OHLCV fetch. Run `make login`.")
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


def bootstrap_data() -> None:
    all_instruments = load_orders_into_store()
    load_market_data_into_store(list(all_instruments.values()))
    store.clear_daily_reports()
    store.clear_instrument_reports()
    store.clear_yearly_reports()
    store.clear_intraday_candles()
