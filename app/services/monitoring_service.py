from __future__ import annotations

from app.db import mongo_store


def get_summary(window_minutes: int) -> dict:
    return mongo_store.get_monitoring_summary(window_minutes=window_minutes)


def get_alerts(window_minutes: int) -> dict:
    summary = get_summary(window_minutes)
    return {"window_minutes": window_minutes, "alerts": summary.get("alerts", [])}


def render_dashboard_html(window_minutes: int) -> str:
    summary = get_summary(window_minutes)
    alerts_html = "".join(
        f"<li><strong>{alert['severity'].upper()}</strong>: {alert['message']}</li>"
        for alert in summary.get("alerts", [])
    ) or "<li>No active alerts.</li>"
    routes_html = "".join(
        "<tr>"
        f"<td>{route['path']}</td>"
        f"<td>{route['requests']}</td>"
        f"<td>{route['failure_count']}</td>"
        f"<td>{route['avg_latency_ms']}</td>"
        f"<td>{route['max_latency_ms']}</td>"
        f"<td>{route['last_status_code']}</td>"
        "</tr>"
        for route in summary.get("api", {}).get("routes", [])
    ) or "<tr><td colspan='6'>No API traffic in this window.</td></tr>"
    llm_html = "".join(
        "<tr>"
        f"<td>{op['operation']}</td>"
        f"<td>{op['calls']}</td>"
        f"<td>{op['failure_count']}</td>"
        f"<td>{op['avg_latency_ms']}</td>"
        f"<td>{op['model']}</td>"
        "</tr>"
        for op in summary.get("llm", {}).get("operations", [])
    ) or "<tr><td colspan='5'>No LLM calls in this window.</td></tr>"
    return f"""
    <html>
      <head>
        <title>Trade Journal Monitoring</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 32px; background: #f5f7fb; color: #162033; }}
          .cards {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
          .card {{ background: white; border-radius: 12px; padding: 16px 20px; min-width: 220px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); }}
          table {{ width: 100%; border-collapse: collapse; background: white; margin-bottom: 24px; }}
          th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e6ebf2; }}
          h1, h2 {{ margin-bottom: 12px; }}
          ul {{ background: white; padding: 16px 24px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); }}
        </style>
      </head>
      <body>
        <h1>Trade Journal Monitoring</h1>
        <p>Window: last {window_minutes} minute(s)</p>
        <div class="cards">
          <div class="card"><strong>Mongo</strong><br>{summary.get('mongo_connected')}</div>
          <div class="card"><strong>API Requests</strong><br>{summary.get('api', {}).get('total_requests')}</div>
          <div class="card"><strong>API Avg Latency</strong><br>{summary.get('api', {}).get('avg_latency_ms')} ms</div>
          <div class="card"><strong>LLM Calls</strong><br>{summary.get('llm', {}).get('total_calls')}</div>
          <div class="card"><strong>LLM Avg Latency</strong><br>{summary.get('llm', {}).get('avg_latency_ms')} ms</div>
        </div>
        <h2>Alerts</h2>
        <ul>{alerts_html}</ul>
        <h2>API Routes</h2>
        <table>
          <tr><th>Path</th><th>Requests</th><th>Failures</th><th>Avg ms</th><th>Max ms</th><th>Last Status</th></tr>
          {routes_html}
        </table>
        <h2>LLM Workflows</h2>
        <table>
          <tr><th>Operation</th><th>Calls</th><th>Failures</th><th>Avg ms</th><th>Model</th></tr>
          {llm_html}
        </table>
      </body>
    </html>
    """
