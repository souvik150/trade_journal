# Backend Monitor Service

This service runs separately from the main API and watches whether the backend is alive.

## What it does

- polls `BACKEND_HEALTH_URL` on a fixed interval
- sends a Slack alert when the backend goes down
- sends a recovery Slack alert when the backend comes back up
- exposes its own status endpoints on port `8001` by default

## Endpoints

- `GET /health`
- `GET /status`

## Environment

Set these in `.env`:

```env
BACKEND_HEALTH_URL=http://localhost:8000/health
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
MONITOR_INTERVAL_SECONDS=30
MONITOR_TIMEOUT_SECONDS=5
MONITOR_PORT=8001
```

## Run

```bash
make run-monitor
```

Or directly:

```bash
python3 scripts/backend_monitor.py
```

## Example

If the backend on `http://localhost:8000/health` stops responding, the monitor stays up on `http://localhost:8001` and sends a Slack alert. You can inspect current state with:

```bash
curl http://localhost:8001/status
```
