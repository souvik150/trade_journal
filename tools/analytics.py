"""
Analytics helpers: pair buy/sell legs, compute P&L and session stats.

Assumptions
-----------
- Prices stored in paise (NSE native units); divide by 100 for ₹.
- Pairing uses FIFO within each instrument (display_name), sorted by filled_time.
- Intraday only: all orders share delivery_type ORDER_DELIVERY_TYPE_IDAY.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns_to_ist(ts_ns: int) -> datetime | None:
    if not ts_ns:
        return None
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=IST)


def _fmt_time(ts_ns: int) -> str | None:
    dt = _ns_to_ist(ts_ns)
    return dt.strftime("%H:%M") if dt else None


def _paise_to_rupees(value: int | float | None) -> float | None:
    if value is None:
        return None
    return round(value / 100.0, 2)


# ---------------------------------------------------------------------------
# Trade pairing
# ---------------------------------------------------------------------------

def pair_trades(orders: list[dict]) -> list[dict]:
    """
    FIFO-pair BUY and SELL legs per instrument (display_name).
    Returns a list of completed trade dicts.
    """
    filled = [o for o in orders if o["execution_status"] == "EXECUTION_STATUS_FILLED"]
    filled.sort(key=lambda o: o["filled_time"])

    # queues of unmatched open legs per instrument
    buy_queues:  dict[str, list[dict]] = defaultdict(list)
    sell_queues: dict[str, list[dict]] = defaultdict(list)
    trades: list[dict] = []

    for order in filled:
        instrument = order["display_name"]
        side       = order["side"]

        if side == "ORDER_SIDE_BUY":
            if sell_queues[instrument]:           # closes a short
                entry_o = sell_queues[instrument].pop(0)
                trades.append(_make_trade(entry_o, order, "SHORT"))
            else:
                buy_queues[instrument].append(order)

        else:                                     # SELL
            if buy_queues[instrument]:            # closes a long
                entry_o = buy_queues[instrument].pop(0)
                trades.append(_make_trade(entry_o, order, "LONG"))
            else:
                sell_queues[instrument].append(order)

    return trades


def _make_trade(entry_o: dict, exit_o: dict, direction: str) -> dict:
    ep = entry_o["order_params"]
    xp = exit_o["order_params"]

    entry_price_paise = ep["avg_fill_price"]
    exit_price_paise  = xp["avg_fill_price"]
    qty         = min(ep["filled_qty"], xp["filled_qty"])

    if direction == "LONG":
        pnl_paise = (exit_price_paise - entry_price_paise) * qty
    else:                                          # SHORT
        pnl_paise = (entry_price_paise - exit_price_paise) * qty

    pnl_rs   = round(pnl_paise / 100.0, 2)
    entry_rs = _paise_to_rupees(entry_price_paise)
    exit_rs  = _paise_to_rupees(exit_price_paise)

    return {
        "entry_order_id":  entry_o["id"],
        "exit_order_id":   exit_o["id"],
        "instrument":      entry_o["display_name"],
        "asset":           ep["asset"],
        "asset_type":      ep["asset_type"],
        "derivative_type": ep["derivative_type"],
        "direction":       direction,
        "entry_time":      _fmt_time(entry_o["filled_time"]),
        "entry_price":     entry_rs,
        "exit_time":       _fmt_time(exit_o["filled_time"]),
        "exit_price":      exit_rs,
        "qty":             qty,
        "pnl":             pnl_rs,
        "strategy":        None,    # will be filled by CrewAI
        "emotion":         None,    # will be filled by CrewAI
    }


# ---------------------------------------------------------------------------
# Daily stats
# ---------------------------------------------------------------------------

def daily_stats(trades: list[dict]) -> dict:
    if not trades:
        return {
            "net_pnl": 0, "trades": 0, "win_rate": 0.0,
            "wins": 0, "losses": 0,
            "best_trade_pnl": None, "worst_trade_pnl": None,
            "max_drawdown": 0,
        }

    pnls  = [t["pnl"] for t in trades]
    wins  = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    net_pnl   = round(sum(pnls), 2)
    win_rate  = round(len(wins) / len(pnls) * 100, 1)

    # max drawdown from cumulative P&L peak
    cumulative, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    return {
        "net_pnl":         net_pnl,
        "trades":          len(trades),
        "win_rate":        win_rate,
        "wins":            len(wins),
        "losses":          len(losses),
        "best_trade_pnl":  round(max(pnls), 2),
        "worst_trade_pnl": round(min(pnls), 2),
        "max_drawdown":    round(-max_dd, 2),
    }


# ---------------------------------------------------------------------------
# Monthly summary
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# P&L flow (cumulative time-series for a single day)
# ---------------------------------------------------------------------------

def pnl_flow(trades: list[dict]) -> tuple[list[dict], dict]:
    """
    Build a cumulative P&L time-series sorted by exit_time.

    Returns (series, stats):
      series : [{"time": "HH:MM", "cumulative_pnl": float}, ...]
               starts at 09:15 with 0, ends at 15:30 with final value
      stats  : avg_win, avg_loss, profit_factor
    """
    # sort trades by exit time (HH:MM string sorts correctly)
    sorted_trades = sorted(
        [t for t in trades if t["exit_time"]],
        key=lambda t: t["exit_time"],
    )

    series: list[dict] = [{"time": "09:15", "cumulative_pnl": 0}]
    cumulative = 0.0
    for t in sorted_trades:
        cumulative += t["pnl"]
        series.append({"time": t["exit_time"], "cumulative_pnl": round(cumulative, 2)})
    if series[-1]["time"] != "15:30":
        series.append({"time": "15:30", "cumulative_pnl": round(cumulative, 2)})

    pnls   = [t["pnl"] for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    avg_win  = round(sum(wins)   / len(wins),   2) if wins   else None
    avg_loss = round(sum(losses) / len(losses), 2) if losses else None
    profit_factor = (
        round(sum(wins) / abs(sum(losses)), 2)
        if losses and sum(losses) != 0 else None
    )

    stats = {
        "avg_win":       avg_win,
        "avg_loss":      avg_loss,
        "profit_factor": profit_factor,
    }
    return series, stats


# ---------------------------------------------------------------------------
# Time-window performance
# ---------------------------------------------------------------------------

_WINDOWS = [
    ("09:15", "10:30"),
    ("10:30", "12:00"),
    ("12:00", "13:30"),
    ("13:30", "15:30"),
]


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def time_window_stats(trades: list[dict]) -> list[dict]:
    """
    Bucket trades into 4 session windows by exit_time.
    Returns list of window dicts with pnl, trade count, wins, losses.
    """
    buckets: list[dict] = []
    for start, end in _WINDOWS:
        start_m = _hhmm_to_minutes(start)
        end_m   = _hhmm_to_minutes(end)

        window_trades = [
            t for t in trades
            if t.get("exit_time")
            and start_m <= _hhmm_to_minutes(t["exit_time"]) < end_m
        ]

        pnls   = [t["pnl"] for t in window_trades]
        wins   = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)

        buckets.append({
            "label":   f"{start}-{end}",
            "pnl":     round(sum(pnls), 2) if pnls else 0,
            "trades":  len(window_trades),
            "wins":    wins,
            "losses":  losses,
            "tag":     None,   # filled after all buckets are computed
        })

    # tag best and worst
    with_trades = [b for b in buckets if b["trades"] > 0]
    if with_trades:
        best  = max(with_trades, key=lambda b: b["pnl"])
        worst = min(with_trades, key=lambda b: b["pnl"])
        if best["pnl"] > 0:
            best["tag"]  = "Best"
        if worst["pnl"] < 0:
            worst["tag"] = "Worst"

    return buckets


# ---------------------------------------------------------------------------
# Monthly summary
# ---------------------------------------------------------------------------

def monthly_summary(day_rows: list[dict]) -> dict:
    """
    Aggregate per-day stats into a month-level summary.
    `day_rows` is the list returned by /journal/monthly → days[].
    """
    trading_days = [d for d in day_rows if d["trades"] > 0]
    if not trading_days:
        return {
            "net_pnl": 0, "trading_days": 0, "win_rate": 0.0,
            "best_day": None, "worst_day": None,
        }

    all_pnls    = [d["net_pnl"]   for d in trading_days]
    all_wr      = [d["win_rate"]  for d in trading_days]
    total_wins  = sum(d["wins"]   for d in trading_days)
    total_trades = sum(d["trades"] for d in trading_days)

    best  = max(trading_days, key=lambda d: d["net_pnl"])
    worst = min(trading_days, key=lambda d: d["net_pnl"])

    return {
        "net_pnl":      round(sum(all_pnls), 2),
        "trading_days": len(trading_days),
        "win_rate":     round(total_wins / total_trades * 100, 1) if total_trades else 0.0,
        "best_day":     {"date": best["date"],  "pnl": best["net_pnl"],  "label": best.get("best_instrument")},
        "worst_day":    {"date": worst["date"], "pnl": worst["net_pnl"], "label": worst.get("worst_instrument")},
    }
