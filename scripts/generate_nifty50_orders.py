"""
Generate realistic synthetic stock orders for one year of trading days.

The generator:
- fetches 1d OHLCV for a fixed Nifty 50 stock universe
- creates 10-12 instruments worth of orders per trading day
- emits a mix of IDAY round-trips and CNC carry trades
- writes one JSON file per day using the same order schema as the app

Usage examples:
  python3 scripts/generate_nifty50_orders.py
  python3 scripts/generate_nifty50_orders.py --output-dir data/generated_orders
  python3 scripts/generate_nifty50_orders.py --output-dir data --clear-output
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import islice
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from nubra_python_sdk.marketdata.market_data import MarketData
from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv


IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_OUTPUT_DIR = DATA_DIR / "generated_orders"
FIELDS = ["open", "high", "low", "close", "cumulative_volume"]
BATCH_SIZE = 5

NIFTY50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]


@dataclass
class Candle:
    symbol: str
    trade_date: date
    open: int
    high: int
    low: int
    close: int
    volume: int | None


@dataclass
class OpenCncPosition:
    symbol: str
    qty: int
    entry_date: date
    target_hold_days: int


def _batched(items: Iterable[str], size: int) -> Iterable[list[str]]:
    iterator = iter(items)
    while chunk := list(islice(iterator, size)):
        yield chunk


def _utc_range(start_day: date, end_day: date) -> tuple[str, str]:
    start_dt = datetime.combine(start_day, time.min).replace(tzinfo=IST).astimezone(ZoneInfo("UTC"))
    end_dt = datetime.combine(end_day + timedelta(days=1), time.min).replace(tzinfo=IST).astimezone(ZoneInfo("UTC"))
    return (
        start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    )


def _timestamp_ns(trade_day: date, hhmm: str) -> int:
    hour, minute = map(int, hhmm.split(":"))
    dt = datetime.combine(trade_day, time(hour=hour, minute=minute), tzinfo=IST)
    return int(dt.timestamp() * 1_000_000_000)


def _clamp(value: float, low: int, high: int) -> int:
    if low > high:
        low, high = high, low
    return int(round(min(max(value, low), high)))


def _pick_time(rng: random.Random, start_h: int, start_m: int, end_h: int, end_m: int) -> str:
    start_total = start_h * 60 + start_m
    end_total = end_h * 60 + end_m
    minute = rng.randint(start_total, end_total)
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _next_time(hhmm: str, add_min: int, latest: str = "15:20") -> str:
    current = int(hhmm[:2]) * 60 + int(hhmm[3:])
    upper = int(latest[:2]) * 60 + int(latest[3:])
    minute = min(current + add_min, upper)
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _price_near_open(candle: Candle, rng: random.Random, low_bias: float, high_bias: float) -> int:
    day_range = max(candle.high - candle.low, 1)
    offset = rng.uniform(low_bias, high_bias) * day_range
    return _clamp(candle.open + offset, candle.low, candle.high)


def _iday_prices(candle: Candle, direction: str, rng: random.Random) -> tuple[int, int]:
    bullish = candle.close >= candle.open
    day_range = max(candle.high - candle.low, max(int(candle.open * 0.004), 1))

    if direction == "LONG":
        if bullish:
            entry = _price_near_open(candle, rng, -0.10, 0.15)
            exit_price = _clamp(entry + rng.uniform(0.05, 0.35) * day_range, candle.low, candle.high)
        else:
            entry = _price_near_open(candle, rng, -0.15, 0.10)
            exit_price = _clamp(entry + rng.uniform(-0.18, 0.12) * day_range, candle.low, candle.high)
    else:
        if bullish:
            entry = _price_near_open(candle, rng, -0.05, 0.18)
            exit_price = _clamp(entry - rng.uniform(-0.10, 0.20) * day_range, candle.low, candle.high)
        else:
            entry = _price_near_open(candle, rng, -0.05, 0.15)
            exit_price = _clamp(entry - rng.uniform(0.05, 0.35) * day_range, candle.low, candle.high)

    if exit_price == entry:
        if direction == "LONG":
            exit_price = _clamp(entry + 1, candle.low, candle.high)
        else:
            exit_price = _clamp(entry - 1, candle.low, candle.high)

    return entry, exit_price


def _cnc_buy_price(candle: Candle, rng: random.Random) -> int:
    return _price_near_open(candle, rng, -0.18, 0.22)


def _cnc_sell_price(candle: Candle, rng: random.Random) -> int:
    return _price_near_open(candle, rng, -0.12, 0.18)


def _build_order(
    *,
    order_id: int,
    symbol: str,
    filled_time_ns: int,
    side: str,
    qty: int,
    fill_price: int,
    delivery_type: str,
    rng: random.Random,
    ltp: int,
) -> dict:
    creation_time = filled_time_ns - rng.randint(1, 6) * 1_000_000_000
    last_modified_time = filled_time_ns
    validity_type = "IOC" if delivery_type == "ORDER_DELIVERY_TYPE_IDAY" else "DAY"
    price_type = "LIMIT" if rng.random() < 0.75 else "MARKET"

    return {
        "id": order_id,
        "delivery_type": delivery_type,
        "execution_type": "EXECUTION_TYPE_REGULAR",
        "side": side,
        "price_type": price_type,
        "qty": qty,
        "execution_status": "EXECUTION_STATUS_FILLED",
        "last_modified_time": last_modified_time,
        "filled_time": filled_time_ns,
        "creation_time": creation_time,
        "display_name": symbol,
        "order_params": {
            "order_price": fill_price,
            "avg_fill_price": fill_price,
            "qty": qty,
            "filled_qty": qty,
            "zanskar_id": rng.randint(1000, 9999),
            "ref_id": rng.randint(100000, 999999),
            "lot_size": 1,
            "asset_type": "STOCKS",
            "derivative_type": "STOCK",
            "asset": symbol,
            "stock_name": symbol,
            "exchange_order_id": order_id,
            "validity_type": validity_type,
            "order_expiry_date": None,
            "side": "",
            "expiry": 0,
            "strike_price": 0,
        },
        "ltp": ltp,
        "exchange": "NSE",
        "is_sor": False,
        "_synthetic": True,
    }


def fetch_daily_ohlc(
    symbols: list[str],
    start_day: date,
    end_day: date,
) -> dict[str, dict[date, Candle]]:
    start_utc, end_utc = _utc_range(start_day, end_day)
    client = MarketData(InitNubraSdk(NubraEnv.PROD, env_creds=True))
    candles_by_symbol: dict[str, dict[date, Candle]] = {symbol: {} for symbol in symbols}

    def request_batch(batch: list[str]):
        return client.historical_data({
            "exchange": "NSE",
            "type": "STOCK",
            "values": batch,
            "fields": FIELDS,
            "startDate": start_utc,
            "endDate": end_utc,
            "interval": "1d",
            "intraDay": False,
            "realTime": False,
        })

    for batch in _batched(symbols, BATCH_SIZE):
        responses = []
        try:
            responses = [request_batch(batch)]
        except Exception as exc:
            print(f"[WARN] Batch fetch failed for {batch}: {exc}")
            for symbol in batch:
                try:
                    responses.append(request_batch([symbol]))
                except Exception as symbol_exc:
                    print(f"[WARN] Skipping unsupported or unavailable symbol {symbol}: {symbol_exc}")

        for response in responses:
            if isinstance(response, dict):
                print(f"[WARN] Nubra returned error payload: {response}")
                continue
            if not response.result:
                continue

            for chart_data in response.result:
                for stock_dict in chart_data.values:
                    for symbol, series in stock_dict.items():
                        opens = series.open or []
                        highs = series.high or []
                        lows = series.low or []
                        closes = series.close or []
                        volumes = series.cumulative_volume or []
                        for idx, open_point in enumerate(opens):
                            trade_day = datetime.fromtimestamp(
                                open_point.timestamp / 1_000_000_000,
                                tz=ZoneInfo("UTC"),
                            ).astimezone(IST).date()
                            candle = Candle(
                                symbol=symbol,
                                trade_date=trade_day,
                                open=int(open_point.value),
                                high=int(highs[idx].value if idx < len(highs) else open_point.value),
                                low=int(lows[idx].value if idx < len(lows) else open_point.value),
                                close=int(closes[idx].value if idx < len(closes) else open_point.value),
                                volume=int(volumes[idx].value) if idx < len(volumes) and volumes[idx].value is not None else None,
                            )
                            candles_by_symbol[symbol][trade_day] = candle

    return candles_by_symbol


def _generate_iday_orders(
    *,
    symbol: str,
    candle: Candle,
    trade_day: date,
    order_id_seq: list[int],
    rng: random.Random,
) -> list[dict]:
    direction = "LONG" if rng.random() < (0.58 if candle.close >= candle.open else 0.42) else "SHORT"
    qty = rng.choice([5, 10, 15, 20, 25, 40, 50, 75, 100, 125])
    entry_time = _pick_time(rng, 9, 20, 14, 20)
    exit_time = _next_time(entry_time, rng.randint(12, 110))
    entry_price, exit_price = _iday_prices(candle, direction, rng)
    ltp = candle.close

    entry_side = "ORDER_SIDE_BUY" if direction == "LONG" else "ORDER_SIDE_SELL"
    exit_side = "ORDER_SIDE_SELL" if direction == "LONG" else "ORDER_SIDE_BUY"

    entry_id = order_id_seq[0]
    order_id_seq[0] += 1
    exit_id = order_id_seq[0]
    order_id_seq[0] += 1

    return [
        _build_order(
            order_id=entry_id,
            symbol=symbol,
            filled_time_ns=_timestamp_ns(trade_day, entry_time),
            side=entry_side,
            qty=qty,
            fill_price=entry_price,
            delivery_type="ORDER_DELIVERY_TYPE_IDAY",
            rng=rng,
            ltp=ltp,
        ),
        _build_order(
            order_id=exit_id,
            symbol=symbol,
            filled_time_ns=_timestamp_ns(trade_day, exit_time),
            side=exit_side,
            qty=qty,
            fill_price=exit_price,
            delivery_type="ORDER_DELIVERY_TYPE_IDAY",
            rng=rng,
            ltp=ltp,
        ),
    ]


def _generate_cnc_buy_order(
    *,
    symbol: str,
    candle: Candle,
    trade_day: date,
    order_id_seq: list[int],
    rng: random.Random,
) -> tuple[dict, OpenCncPosition]:
    qty = rng.choice([1, 2, 3, 4, 5, 8, 10, 12, 15, 20])
    buy_time = _pick_time(rng, 9, 25, 14, 50)
    buy_price = _cnc_buy_price(candle, rng)
    order_id = order_id_seq[0]
    order_id_seq[0] += 1

    order = _build_order(
        order_id=order_id,
        symbol=symbol,
        filled_time_ns=_timestamp_ns(trade_day, buy_time),
        side="ORDER_SIDE_BUY",
        qty=qty,
        fill_price=buy_price,
        delivery_type="ORDER_DELIVERY_TYPE_CNC",
        rng=rng,
        ltp=candle.close,
    )
    position = OpenCncPosition(
        symbol=symbol,
        qty=qty,
        entry_date=trade_day,
        target_hold_days=rng.randint(2, 12),
    )
    return order, position


def _generate_cnc_sell_order(
    *,
    position: OpenCncPosition,
    candle: Candle,
    trade_day: date,
    order_id_seq: list[int],
    rng: random.Random,
) -> dict:
    sell_time = _pick_time(rng, 10, 0, 15, 15)
    sell_price = _cnc_sell_price(candle, rng)
    order_id = order_id_seq[0]
    order_id_seq[0] += 1

    return _build_order(
        order_id=order_id,
        symbol=position.symbol,
        filled_time_ns=_timestamp_ns(trade_day, sell_time),
        side="ORDER_SIDE_SELL",
        qty=position.qty,
        fill_price=sell_price,
        delivery_type="ORDER_DELIVERY_TYPE_CNC",
        rng=rng,
        ltp=candle.close,
    )


def generate_orders(
    candles_by_symbol: dict[str, dict[date, Candle]],
    *,
    seed: int,
    min_symbols_per_day: int,
    max_symbols_per_day: int,
) -> dict[date, list[dict]]:
    rng = random.Random(seed)
    trading_days = sorted({day for series in candles_by_symbol.values() for day in series})
    open_cnc_positions: dict[str, OpenCncPosition] = {}
    orders_by_day: dict[date, list[dict]] = {}
    order_id_seq = [700000]

    for trade_day in trading_days:
        available_symbols = [
            symbol for symbol, series in candles_by_symbol.items()
            if trade_day in series
        ]
        if len(available_symbols) < min_symbols_per_day:
            continue

        target_symbols = rng.randint(min_symbols_per_day, max_symbols_per_day)
        day_orders: list[dict] = []

        closable_positions = [
            pos for pos in open_cnc_positions.values()
            if pos.symbol in candles_by_symbol
            and trade_day in candles_by_symbol[pos.symbol]
            and (trade_day - pos.entry_date).days >= pos.target_hold_days
        ]
        rng.shuffle(closable_positions)

        selected_symbols: list[str] = []
        close_count = min(len(closable_positions), rng.randint(1, min(3, max(1, target_symbols // 4))))
        for position in closable_positions[:close_count]:
            if position.symbol not in selected_symbols:
                selected_symbols.append(position.symbol)
                candle = candles_by_symbol[position.symbol][trade_day]
                day_orders.append(
                    _generate_cnc_sell_order(
                        position=position,
                        candle=candle,
                        trade_day=trade_day,
                        order_id_seq=order_id_seq,
                        rng=rng,
                    )
                )
                open_cnc_positions.pop(position.symbol, None)

        remaining_pool = [symbol for symbol in available_symbols if symbol not in selected_symbols]
        rng.shuffle(remaining_pool)
        for symbol in remaining_pool:
            if len(selected_symbols) >= target_symbols:
                break
            selected_symbols.append(symbol)

        for symbol in selected_symbols[close_count:]:
            candle = candles_by_symbol[symbol][trade_day]
            use_cnc = symbol not in open_cnc_positions and rng.random() < 0.22
            if use_cnc:
                cnc_buy_order, position = _generate_cnc_buy_order(
                    symbol=symbol,
                    candle=candle,
                    trade_day=trade_day,
                    order_id_seq=order_id_seq,
                    rng=rng,
                )
                day_orders.append(cnc_buy_order)
                open_cnc_positions[symbol] = position
            else:
                day_orders.extend(
                    _generate_iday_orders(
                        symbol=symbol,
                        candle=candle,
                        trade_day=trade_day,
                        order_id_seq=order_id_seq,
                        rng=rng,
                    )
                )

        day_orders.sort(key=lambda order: order["filled_time"])
        orders_by_day[trade_day] = day_orders

    return orders_by_day


def write_orders(
    orders_by_day: dict[date, list[dict]],
    *,
    output_dir: Path,
    clear_output: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if clear_output:
        for path in output_dir.glob("orders_*.json"):
            path.unlink()

    for trade_day, orders in orders_by_day.items():
        path = output_dir / f"orders_{trade_day.isoformat()}.json"
        path.write_text(json.dumps(orders, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=str, default=None, help="Start date in YYYY-MM-DD. Default: one year back from end date.")
    parser.add_argument("--end-date", type=str, default=None, help="End date in YYYY-MM-DD. Default: today.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic output.")
    parser.add_argument("--min-symbols-per-day", type=int, default=10, help="Minimum unique instruments per day.")
    parser.add_argument("--max-symbols-per-day", type=int, default=12, help="Maximum unique instruments per day.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory to write orders_YYYY-MM-DD.json files into.")
    parser.add_argument("--clear-output", action="store_true", help="Delete existing orders_*.json files in the output directory first.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end_day = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start_day = date.fromisoformat(args.start_date) if args.start_date else (end_day - timedelta(days=365))

    if args.min_symbols_per_day < 1 or args.max_symbols_per_day < args.min_symbols_per_day:
        raise ValueError("Invalid per-day symbol range.")

    candles_by_symbol = fetch_daily_ohlc(NIFTY50_SYMBOLS, start_day, end_day)
    orders_by_day = generate_orders(
        candles_by_symbol,
        seed=args.seed,
        min_symbols_per_day=args.min_symbols_per_day,
        max_symbols_per_day=args.max_symbols_per_day,
    )
    write_orders(
        orders_by_day,
        output_dir=args.output_dir,
        clear_output=args.clear_output,
    )

    print(
        f"Wrote {len(orders_by_day)} trading-day files to {args.output_dir} "
        f"for range {start_day} -> {end_day}."
    )


if __name__ == "__main__":
    main()
