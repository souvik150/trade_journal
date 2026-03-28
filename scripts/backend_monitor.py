from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BACKEND_HEALTH_URL = os.getenv("BACKEND_HEALTH_URL", "http://localhost:8000/health")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "30"))
MONITOR_TIMEOUT_SECONDS = float(os.getenv("MONITOR_TIMEOUT_SECONDS", "5"))
MONITOR_PORT = int(os.getenv("MONITOR_PORT", "8001"))

state: dict[str, object] = {
    "backend_url": BACKEND_HEALTH_URL,
    "status": "unknown",
    "consecutive_failures": 0,
    "last_checked_at": None,
    "last_success_at": None,
    "last_failure_at": None,
    "last_error": None,
    "last_alert_at": None,
    "last_recovery_at": None,
}

_stop_event = threading.Event()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _send_slack(message: str) -> None:
    if not SLACK_WEBHOOK_URL:
        logger.warning("SLACK_WEBHOOK_URL not set; skipping Slack alert: %s", message)
        return

    try:
        response = httpx.post(
            SLACK_WEBHOOK_URL,
            json={"text": message},
            timeout=MONITOR_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        state["last_alert_at"] = _utc_now()
    except Exception as exc:
        logger.warning("Failed to send Slack alert: %s", exc)


def _check_backend_once() -> None:
    state["last_checked_at"] = _utc_now()

    try:
        response = httpx.get(BACKEND_HEALTH_URL, timeout=MONITOR_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:
        state["consecutive_failures"] = int(state["consecutive_failures"]) + 1
        state["last_failure_at"] = _utc_now()
        state["last_error"] = str(exc)

        if state["status"] != "down":
            state["status"] = "down"
            _send_slack(
                f":red_circle: Trade Journal backend is DOWN.\n"
                f"URL: {BACKEND_HEALTH_URL}\n"
                f"Error: {exc}"
            )
        return

    was_down = state["status"] == "down"
    state["status"] = "up"
    state["consecutive_failures"] = 0
    state["last_success_at"] = _utc_now()
    state["last_error"] = None

    if was_down:
        state["last_recovery_at"] = _utc_now()
        _send_slack(
            f":large_green_circle: Trade Journal backend recovered.\n"
            f"URL: {BACKEND_HEALTH_URL}"
        )


def _monitor_loop() -> None:
    logger.info("Backend monitor started for %s", BACKEND_HEALTH_URL)
    while not _stop_event.is_set():
        _check_backend_once()
        _stop_event.wait(MONITOR_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    worker = threading.Thread(target=_monitor_loop, daemon=True)
    worker.start()
    yield
    _stop_event.set()
    worker.join(timeout=2)


app = FastAPI(title="Trade Journal Backend Monitor", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "backend-monitor",
        "backend_status": state["status"],
    }


@app.get("/status")
def status():
    return dict(state)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=MONITOR_PORT,
        reload=False,
        log_level="info",
        access_log=True,
    )
