# Trade Journal AI — API Reference

Base URL: `http://localhost:8000`

---

## Bootstrap

### `POST /data/load`
Manually rebuilds in-memory stock-order data and OHLCV.
The server already does this automatically on startup, so normal API usage does not require this route. Daily and yearly caches are generated lazily when first requested and persisted in MongoDB when available.

```bash
curl -X POST http://localhost:8000/data/load
```

**Response**
```json
{
  "status": "rebuilt",
  "dates_loaded": 15,
  "reports_cached": 0,
  "symbols_with_ohlcv": ["AXISBANK", "HDFCBANK", "ICICIBANK", "INFY", "RELIANCE", "SBIN", "TCS", "WIPRO"],
  "total_orders": 327
}
```

---

## 1. Monthly Journal Calendar

### `GET /journal/monthly`

Returns per-day P&L, trade count, and win rate for every trading day in the month, plus a month-level summary.

**Query params**
| Param | Type | Required | Example |
|-------|------|----------|---------|
| `year` | int | yes | `2025` |
| `month` | int | yes | `3` |

```bash
curl "http://localhost:8000/journal/monthly?year=2025&month=3"
```

**Response**
```json
{
  "year": 2025,
  "month": 3,
  "summary": {
    "net_pnl": -696895,
    "trading_days": 15,
    "win_rate": 44.2,
    "best_day":  { "date": "2025-03-13", "pnl": 661560, "label": "NIFTY 30 Mar 22900 CE" },
    "worst_day": { "date": "2025-03-17", "pnl": -568835, "label": "NIFTY 30 Mar FUT" }
  },
  "days": [
    {
      "date": "2025-03-09",
      "net_pnl": -367905,
      "trades": 6,
      "wins": 2,
      "losses": 4,
      "win_rate": 33.3,
      "best_trade_pnl": 12500,
      "worst_trade_pnl": -89000,
      "max_drawdown": -89000,
      "best_instrument": "SBIN",
      "worst_instrument": "NIFTY 30 Mar FUT"
    }
  ]
}
```

## 2. Daily Trade Log

### `GET /journal/daily`

Returns the cached daily report for a single day. If that date has not been requested before, the server generates it once, persists it in MongoDB when available, and reuses it for later requests so repeat calls do not hit OpenAI again.

**Query params**
| Param | Type | Required | Example |
|-------|------|----------|---------|
| `date` | str | yes | `2025-03-26` |

```bash
curl "http://localhost:8000/journal/daily?date=2025-03-26"
```

**Response**
```json
{
  "date": "2025-03-26",
  "stats": {
    "net_pnl": 45695,
    "trades": 5,
    "wins": 2,
    "losses": 2,
    "win_rate": 40.0,
    "best_trade_pnl": 40625,
    "max_drawdown": -330
  },
  "smart_insight": [
    "You handled the opening sequence well and kept your better trades aligned with the stronger early-session moves.",
    "Your weaker executions came from entries that lagged the move instead of joining it near the first clean trigger.",
    "The clearest repeat pattern is stronger performance in the first half of the session than in late, slower trade windows."
  ],
  "trades": [
    {
      "trade_no": 1,
      "instrument": "AXISBANK",
      "asset": "AXISBANK",
      "asset_type": "STOCKS",
      "derivative_type": "STOCK",
      "direction": "SHORT",
      "entry_time": "09:30",
      "entry_price": 1851,
      "exit_time": "09:46",
      "exit_price": 1224,
      "qty": 65,
      "pnl": 40625,
      "strategy": "ORB breakdown",
      "emotion": "disciplined"
    }
  ]
}
```

### `GET /journal/daily/export`

Downloads the `/journal/daily` payload as a CSV file. The export includes one row per trade, and if the day has no trades it still returns one summary row for that date.

**Query params**
| Param | Type | Required | Example |
|-------|------|----------|---------|
| `date` | str | yes | `2025-03-26` |

```bash
curl -OJ "http://localhost:8000/journal/daily/export?date=2025-03-26"
```

### `GET /journal/daily/report`

Downloads a single PDF report file for the day. The file bundles the day-level report, daily P&L flow, time-performance summary, and each instrument's full detail report for that date.

**Query params**
| Param | Type | Required | Example |
|-------|------|----------|---------|
| `date` | str | yes | `2025-03-26` |

```bash
curl -OJ "http://localhost:8000/journal/daily/report?date=2025-03-26"
```

---

## 3. Intraday P&L Flow

### `GET /journal/daily/pnl-flow`

Per-minute cumulative realised P&L curve across the trading session for all instruments on that day. The payload is cached in MongoDB by `date`, so repeat calls do not recalculate it.

**Query params**
| Param | Type | Required | Example |
|-------|------|----------|---------|
| `date` | str | yes | `2025-03-26` |

```bash
curl "http://localhost:8000/journal/daily/pnl-flow?date=2025-03-26"
```

**Response**
```json
{
  "date": "2025-03-26",
  "closed_pnl": 45695,
  "series": [
    { "time": "09:15", "cumulative_pnl": 0 },
    { "time": "09:16", "cumulative_pnl": 0 },
    { "time": "09:17", "cumulative_pnl": 0 },
    { "time": "09:46", "cumulative_pnl": 40625 },
    { "time": "09:47", "cumulative_pnl": 40625 },
    { "time": "15:30", "cumulative_pnl": 45695 }
  ],
  "stats": {
    "avg_win": 23012.5,
    "avg_loss": -165.0,
    "profit_factor": 139.47
  }
}
```

---

## 4. Time-Window Performance

### `GET /analytics/time-performance`

P&L broken into 4 session buckets. Pass `?date=` for a single day or `?year=&month=` to aggregate the full month.

**Query params** (one group required)
| Param | Type | Example |
|-------|------|---------|
| `date` | str | `2025-03-26` |
| `year` + `month` | int | `2025` + `3` |

```bash
# single day
curl "http://localhost:8000/analytics/time-performance?date=2025-03-26"

# full month
curl "http://localhost:8000/analytics/time-performance?year=2025&month=3"
```

**Response**
```json
{
  "scope": "day",
  "windows": [
    { "label": "09:15-10:30", "pnl": 40625, "trades": 1, "wins": 1, "losses": 0, "tag": "Best" },
    { "label": "10:30-12:00", "pnl":  5400, "trades": 1, "wins": 1, "losses": 0, "tag": null  },
    { "label": "12:00-13:30", "pnl":     0, "trades": 0, "wins": 0, "losses": 0, "tag": null  },
    { "label": "13:30-15:30", "pnl":  -330, "trades": 3, "wins": 0, "losses": 2, "tag": "Worst" }
  ],
  "pattern_insight": null
}
```

---

## 5. Instrument Detail

### `GET /trade`

Instrument-level detail for one trading day. This is the route to open from the daily journal screen because it uses the same `date` and `instrument` values already present in `/journal/daily`.
If the same `date + instrument` is requested again, the API serves the cached result from MongoDB instead of calling OpenAI again.

**Query params**
| Param | Type | Required | Example |
|-------|------|----------|---------|
| `date` | str | yes | `2025-03-26` |
| `instrument` | str | yes | `AXISBANK` |

```bash
curl "http://localhost:8000/trade?date=2025-03-26&instrument=AXISBANK"
```

**Response**
```json
{
  "date": "2025-03-26",
  "instrument": "AXISBANK",
  "asset": "AXISBANK",
  "asset_type": "STOCKS",
  "derivative_type": "STOCK",
  "direction": "SHORT",
  "entry": { "time": "09:30", "price": 18.49 },
  "exit": { "time": "09:46", "price": 12.24 },
  "qty": 65,
  "pnl": 406.25,
  "strategy": "ORB breakdown",
  "emotion": "disciplined",
  "stats": {
    "net_pnl": 406.25,
    "trades": 1,
    "wins": 1,
    "losses": 0,
    "win_rate": 100.0,
    "best_trade_pnl": 406.25,
    "max_drawdown": 0
  },
  "ohlcv_candles": [
    {
      "timestamp_ns": 1741491900000000000,
      "open": 185100,
      "high": 186000,
      "low": 183200,
      "close": 184500,
      "volume": 412300
    }
  ],
  "signal_timeline": [],
  "analysis": {
    "why_it_worked": null,
    "what_could_be_better": null,
    "post_exit_summary": null,
    "lessons": [],
    "trade_narrative": null
  },
  "trades": [
    {
      "trade_no": 1,
      "instrument": "AXISBANK",
      "asset": "AXISBANK",
      "asset_type": "STOCKS",
      "derivative_type": "STOCK",
      "direction": "SHORT",
      "entry_time": "09:30",
      "entry_price": 18.49,
      "exit_time": "09:46",
      "exit_price": 12.24,
      "qty": 65,
      "pnl": 406.25,
      "strategy": "ORB breakdown",
      "emotion": "disciplined"
    }
  ]
}
```

---

## 3. Yearly P&L

### `GET /journal/yearly`

Returns one row per available trading day in the given year, with the daily P&L aggregated from that day’s trades.

**Query params**
| Param | Type | Required | Example |
|-------|------|----------|---------|
| `year` | int | yes | `2025` |

```bash
curl "http://localhost:8000/journal/yearly?year=2025"
```

**Response**
```json
{
  "year": 2025,
  "summary": {
    "net_pnl": 125430,
    "trading_days": 212,
    "total_trades": 1834,
    "winning_days": 109,
    "losing_days": 97,
    "best_day": {
      "date": "2025-09-17",
      "net_pnl": 18250,
      "trades": 9,
      "wins": 6,
      "losses": 3,
      "win_rate": 66.7
    },
    "worst_day": {
      "date": "2025-08-05",
      "net_pnl": -14320,
      "trades": 7,
      "wins": 2,
      "losses": 5,
      "win_rate": 28.6
    }
  },
  "daily_pnl": [
    { "date": "2025-01-02", "pnl": 2150 },
    { "date": "2025-01-03", "pnl": -430 }
  ],
  "days": [
    {
      "date": "2025-01-02",
      "net_pnl": 2150,
      "trades": 8,
      "wins": 5,
      "losses": 3,
      "win_rate": 62.5
    }
  ]
}
```

---

## Health

### `GET /health`

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "dates_loaded": 15,
  "reports_cached": 3,
  "yearly_cached": 1,
  "mongo_connected": true,
  "symbols_with_ohlcv": 8
}
```

---

## Notes

### `GET /notes`

Fetch the saved note for one instrument on one date.

```bash
curl "http://localhost:8000/notes?date=2025-03-26&instrument=AXISBANK"
```

### `POST /notes/text`

Save or replace the note text for one instrument/day. Only one note exists per `date + instrument`.

```bash
curl -X POST http://localhost:8000/notes/text \
  -F date=2025-03-26 \
  -F instrument=AXISBANK \
  -F text="Good follow-through after the open."
```

### `POST /notes/voice`

Upload a voice note, transcribe it with OpenAI, and save or replace the note text. Only one note exists per `date + instrument`.

```bash
curl -X POST http://localhost:8000/notes/voice \
  -F date=2025-03-26 \
  -F instrument=AXISBANK \
  -F file=@voice_note.m4a
```

---

## Monitoring

### `GET /monitoring/summary`

Returns API request counts, average latency, failure counts, LLM workflow counts, and active alerts for a recent time window.

```bash
curl "http://localhost:8000/monitoring/summary?window_minutes=60"
```

### `GET /monitoring/alerts`

Returns only the active alerts for the recent window.

```bash
curl "http://localhost:8000/monitoring/alerts?window_minutes=60"
```

### `GET /monitoring`

HTML dashboard for API health, Mongo connectivity, request latency, LLM usage, and active alerts.

```bash
open "http://localhost:8000/monitoring"
```

---

## Typical flow

```bash
# 1. start server
python3 main.py

# 2. browse the month
curl "http://localhost:8000/journal/monthly?year=2025&month=3"

# 3. drill into a day
curl "http://localhost:8000/journal/daily?date=2025-03-26"

# 4. view P&L curve for that day
curl "http://localhost:8000/journal/daily/pnl-flow?date=2025-03-26"

# 5. check time-window breakdown
curl "http://localhost:8000/analytics/time-performance?date=2025-03-26"

# 6. fetch year-wide daily pnl
curl "http://localhost:8000/journal/yearly?year=2025"

# 7. open one instrument detail screen from the daily journal
curl "http://localhost:8000/trade?date=2025-03-26&instrument=AXISBANK"

# optional: manually rebuild caches after order data changes
curl -X POST http://localhost:8000/data/load
```
