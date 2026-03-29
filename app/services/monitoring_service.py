from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.db import mongo_store

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)


def get_summary(window_minutes: int) -> dict:
    return mongo_store.get_monitoring_summary(window_minutes=window_minutes)


def get_alerts(window_minutes: int) -> dict:
    summary = get_summary(window_minutes)
    return {"window_minutes": window_minutes, "alerts": summary.get("alerts", [])}


def render_dashboard_html(window_minutes: int) -> str:
    summary = get_summary(window_minutes)
    template = _jinja_env.get_template("monitoring.html")
    return template.render(
        window_minutes=window_minutes,
        mongo_connected=summary.get("mongo_connected", False),
        api=summary.get("api", {}),
        llm=summary.get("llm", {}),
        alerts=summary.get("alerts", []),
    )
