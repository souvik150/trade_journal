"""
Fetch historical OHLCV data for all instruments found in orders.json
and print them as pandas DataFrames.

Usage:
    python3 fetch_historical.py
    python3 fetch_historical.py --start 2025-05-09 --end 2025-05-22 --interval 1d
"""

import argparse
import json
from collections import defaultdict
from itertools import islice
from pathlib import Path

import pandas as pd
from nubra_python_sdk.marketdata.market_data import MarketData
from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 1000)
pd.set_option("display.max_colwidth", None)
pd.set_option("display.width", 0)

DATA_DIR = Path(__file__).parent / "data" / "generated_orders"
FIELDS = ["open", "high", "low", "close", "cumulative_volume"]
BATCH_SIZE = 5


def load_instruments():
    order_files = sorted(DATA_DIR.glob("orders_*.json"))
    if not order_files:
        raise FileNotFoundError(f"No orders_*.json files found in {DATA_DIR}")
    seen = {}
    for path in order_files:
        for order in json.loads(path.read_text()):
            params = order.get("order_params", {})
            symbol = params.get("asset") or order.get("display_name")
            exchange = order.get("exchange", "NSE")
            raw_type = params.get("asset_type", "STOCKS")
            nubra_type = raw_type.rstrip("S")  # "STOCKS" → "STOCK"
            if symbol:
                seen[symbol] = {"symbol": symbol, "exchange": exchange, "type": nubra_type}
    return sorted(seen.values(), key=lambda x: x["symbol"])


def batched(iterable, n):
    it = iter(iterable)
    while chunk := list(islice(it, n)):
        yield chunk


def tsp_to_series(tsp_list):
    if not tsp_list:
        return pd.Series(dtype=float)
    return pd.Series(
        data=[p.value for p in tsp_list],
        index=pd.to_datetime([p.timestamp for p in tsp_list], unit="ns", utc=True).tz_convert("Asia/Kolkata"),
    )


def fetch_and_print(instruments, start_date, end_date, interval):
    nubra = InitNubraSdk(NubraEnv.PROD, env_creds=True)
    client = MarketData(nubra)

    print(f"\nFetching historical data")
    print(f"  Range    : {start_date}  →  {end_date}")
    print(f"  Interval : {interval}")
    print(f"  Symbols  : {[i['symbol'] for i in instruments]}")
    print("=" * 70)

    groups = defaultdict(list)
    for inst in instruments:
        groups[(inst["exchange"].upper(), inst["type"].upper())].append(inst["symbol"])

    all_dfs = {}

    for (exchange, inst_type), symbols in groups.items():
        for batch in batched(symbols, BATCH_SIZE):
            print(f"\n[{exchange} / {inst_type}]  fetching: {batch}")
            response = client.historical_data({
                "exchange": exchange,
                "type": inst_type,
                "values": batch,
                "fields": FIELDS,
                "startDate": start_date,
                "endDate": end_date,
                "interval": interval,
                "intraDay": False,
                "realTime": False,
            })

            if isinstance(response, dict):
                print(f"  error: {response}")
                continue

            print(f"  message     : {response.message}")
            print(f"  market_time : {response.market_time}")

            if not response.result:
                print("  (no result data)")
                continue

            for chart_data in response.result:
                for stock_dict in chart_data.values:
                    for symbol, sc in stock_dict.items():
                        open_s  = tsp_to_series(sc.open)
                        high_s  = tsp_to_series(sc.high)
                        low_s   = tsp_to_series(sc.low)
                        close_s = tsp_to_series(sc.close)
                        vol_s   = tsp_to_series(sc.cumulative_volume)

                        if open_s.empty:
                            print(f"\n  {symbol}: no data returned")
                            continue

                        df = pd.DataFrame({
                            "open":               open_s,
                            "high":               high_s,
                            "low":                low_s,
                            "close":              close_s,
                            "cumulative_volume":  vol_s,
                        })
                        df.sort_index(inplace=True)
                        df["symbol"] = symbol
                        all_dfs[symbol] = df

                        print(f"\n  --- {symbol} ({len(df)} rows) ---")
                        print(df.to_string())

    print("\n" + "=" * 70)
    return all_dfs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",    default="2025-03-01T03:30:00.000Z",
                        help="Start datetime UTC (default: 2025-03-01T03:30:00.000Z)")
    parser.add_argument("--end",      default="2025-03-31T11:30:00.000Z",
                        help="End datetime UTC   (default: 2025-03-31T11:30:00.000Z)")
    parser.add_argument("--interval", "-i", default="1d",
                        help="Candle interval    (default: 1d)")
    args = parser.parse_args()

    instruments = load_instruments()
    if not instruments:
        print(f"No instruments found in {DATA_DIR}/orders_*.json")
        return

    fetch_and_print(instruments, args.start, args.end, args.interval)


if __name__ == "__main__":
    main()
