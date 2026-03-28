from __future__ import annotations

import csv
import io

from fastapi import HTTPException


def render_daily_report_csv(daily_report: dict) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "date",
            "net_pnl",
            "trades_count",
            "wins",
            "losses",
            "win_rate",
            "best_trade_pnl",
            "max_drawdown",
            "trade_no",
            "instrument",
            "derivative_type",
            "direction",
            "entry_time",
            "entry_price",
            "exit_time",
            "exit_price",
            "qty",
            "pnl",
            "strategy",
        ],
    )
    writer.writeheader()
    stats = daily_report["stats"]
    base_row = {
        "date": daily_report["date"],
        "net_pnl": stats["net_pnl"],
        "trades_count": stats["trades"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "win_rate": stats["win_rate"],
        "best_trade_pnl": stats["best_trade_pnl"],
        "max_drawdown": stats["max_drawdown"],
    }
    trades = daily_report.get("trades", [])
    if not trades:
        writer.writerow(base_row)
        return output.getvalue()
    for trade in trades:
        writer.writerow({
            **base_row,
            "trade_no": trade.get("trade_no"),
            "instrument": trade.get("instrument"),
            "derivative_type": trade.get("derivative_type"),
            "direction": trade.get("direction"),
            "entry_time": trade.get("entry_time"),
            "entry_price": trade.get("entry_price"),
            "exit_time": trade.get("exit_time"),
            "exit_price": trade.get("exit_price"),
            "qty": trade.get("qty"),
            "pnl": trade.get("pnl"),
            "strategy": trade.get("strategy"),
        })
    return output.getvalue()


def _pdf_write_line(text_object, text: str = "") -> None:
    text_object.textLine((text or "")[:2000])


def _pdf_write_wrapped(text_object, text: str, width: int = 105) -> None:
    if not text:
        _pdf_write_line(text_object, "-")
        return
    words = str(text).split()
    if not words:
        _pdf_write_line(text_object, "-")
        return
    line = words[0]
    for word in words[1:]:
        candidate = f"{line} {word}"
        if len(candidate) <= width:
            line = candidate
        else:
            _pdf_write_line(text_object, line)
            line = word
    _pdf_write_line(text_object, line)


def _start_pdf_page(pdf, title: str):
    from reportlab.lib.pagesizes import A4

    width, height = A4
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(40, height - 40, title)
    pdf.setFont("Helvetica", 10)
    text_object = pdf.beginText(40, height - 65)
    text_object.setLeading(14)
    return text_object, width, height


def _finish_pdf_page(pdf, text_object) -> None:
    pdf.drawText(text_object)
    pdf.showPage()


def render_daily_report_pdf(report: dict) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PDF export requires reportlab to be installed.") from exc

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    date = report["date"]
    text, _, _ = _start_pdf_page(pdf, f"Trade Journal Report - {date}")
    daily = report["daily_report"]
    stats = daily["stats"]
    _pdf_write_line(text, f"Generated at: {report['generated_at']}")
    _pdf_write_line(text)
    _pdf_write_line(text, "Day Summary")
    _pdf_write_line(text, f"Net P&L: {stats['net_pnl']}")
    _pdf_write_line(text, f"Trades: {stats['trades']} | Wins: {stats['wins']} | Losses: {stats['losses']} | Win Rate: {stats['win_rate']}")
    _pdf_write_line(text, f"Best Trade P&L: {stats['best_trade_pnl']} | Max Drawdown: {stats['max_drawdown']}")
    _pdf_write_line(text)
    _pdf_write_line(text, "Smart Insights")
    for insight in daily.get("smart_insight", []):
        _pdf_write_wrapped(text, f"- {insight}")
    _pdf_write_line(text)
    _pdf_write_line(text, "Time Performance")
    for window in report["time_performance"]["windows"]:
        _pdf_write_line(text, f"{window['label']}: pnl={window['pnl']} trades={window['trades']} wins={window['wins']} losses={window['losses']} tag={window.get('tag')}")
    pattern_insight = report["time_performance"].get("pattern_insight")
    if pattern_insight:
        _pdf_write_line(text)
        _pdf_write_wrapped(text, f"Pattern Insight: {pattern_insight}")
    _finish_pdf_page(pdf, text)

    for instrument_item in report["instrument_reports"]:
        instrument_report = instrument_item["report"]
        note = instrument_item.get("note")
        text, _, _ = _start_pdf_page(pdf, f"{instrument_report['instrument']} - {date}")
        instrument_stats = instrument_report["stats"]
        _pdf_write_line(text, f"Direction: {instrument_report.get('direction')}")
        _pdf_write_line(text, f"Entry: {instrument_report['entry']['time']} @ {instrument_report['entry']['price']} | Exit: {instrument_report['exit']['time']} @ {instrument_report['exit']['price']}")
        _pdf_write_line(text, f"Qty: {instrument_report['qty']} | Net P&L: {instrument_report['pnl']}")
        _pdf_write_line(text, f"Trades: {instrument_stats['trades']} | Wins: {instrument_stats['wins']} | Losses: {instrument_stats['losses']} | Win Rate: {instrument_stats['win_rate']}")
        if instrument_report.get("strategy") is not None or instrument_report.get("emotion") is not None:
            _pdf_write_line(text, f"Strategy: {instrument_report.get('strategy')} | Emotion: {instrument_report.get('emotion')}")
        _pdf_write_line(text)
        _pdf_write_line(text, "Signal Timeline")
        for event in instrument_report.get("signal_timeline", []):
            _pdf_write_line(text, f"{event.get('time')} | {event.get('tag')} | {event.get('title')}")
            _pdf_write_wrapped(text, event.get("description", ""))
        _pdf_write_line(text)
        _pdf_write_line(text, "Analysis")
        analysis = instrument_report.get("analysis", {})
        for key in ["why_it_worked", "what_could_be_better", "post_exit_summary", "trade_narrative"]:
            if analysis.get(key):
                label = key.replace("_", " ").title()
                _pdf_write_wrapped(text, f"{label}: {analysis[key]}")
        lessons = analysis.get("lessons") or []
        if lessons:
            _pdf_write_line(text, "Lessons")
            for lesson in lessons:
                _pdf_write_wrapped(text, f"- {lesson}")
        _pdf_write_line(text)
        _pdf_write_line(text, "Trades")
        for trade in instrument_report.get("trades", []):
            _pdf_write_line(text, f"#{trade['trade_no']} {trade['direction']} qty={trade['qty']} entry={trade['entry_time']}@{trade['entry_price']} exit={trade['exit_time']}@{trade['exit_price']} pnl={trade['pnl']}")
        _pdf_write_line(text)
        _pdf_write_line(text, "Note")
        if note:
            _pdf_write_wrapped(text, note.get("text", ""))
        else:
            _pdf_write_line(text, "No note saved.")
        _finish_pdf_page(pdf, text)
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()
