"""
Fetch 1-minute OHLCV candles from Nubra for a single instrument/date.
Returns a compact CSV string ready to be injected into an LLM prompt.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def fetch_intraday_csv(
    symbol: str,
    exchange: str,
    inst_type: str,
    date: str,           # YYYY-MM-DD
) -> str:
    """
    Fetch 1m candles from Nubra PROD for the full trading session.
    Returns CSV lines: time,open,high,low,close,volume
    Returns empty string on any failure (caller handles gracefully).
    """
    try:
        from nubra_python_sdk.marketdata.market_data import MarketData
        from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

        # 09:15–15:30 IST = 03:45–10:00 UTC
        start = f"{date}T03:45:00.000Z"
        end   = f"{date}T10:00:00.000Z"

        # Keep the environment aligned with the login/bootstrap flow.
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
            return ""

        lines = ["time,open,high,low,close,volume"]
        for chart_data in response_result:
            for stock_dict in chart_data.values:
                for _sym, sc in stock_dict.items():
                    if not sc.open:
                        continue
                    for i, pt in enumerate(sc.open):
                        ts = datetime.fromtimestamp(pt.timestamp / 1e9, tz=IST)
                        h  = sc.high[i].value              if sc.high              and i < len(sc.high)              else ""
                        lo = sc.low[i].value               if sc.low               and i < len(sc.low)               else ""
                        c  = sc.close[i].value             if sc.close             and i < len(sc.close)             else ""
                        v  = sc.cumulative_volume[i].value if sc.cumulative_volume and i < len(sc.cumulative_volume) else ""
                        lines.append(f"{ts.strftime('%H:%M')},{pt.value},{h},{lo},{c},{v}")
        return "\n".join(lines)

    except Exception as e:
        print(f"[WARN] intraday fetch failed for {symbol} on {date}: {e}")
        return ""


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
