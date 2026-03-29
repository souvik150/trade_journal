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
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            BaseDocTemplate, Frame, NextPageTemplate,
            PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
        )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PDF export requires reportlab to be installed.") from exc

    # ── Palette ──────────────────────────────────────────────────────────────
    BG       = colors.HexColor("#0d1117")
    SURFACE  = colors.HexColor("#161b22")
    BORDER   = colors.HexColor("#21262d")
    GREEN    = colors.HexColor("#00d084")
    BLUE     = colors.HexColor("#00a8ff")
    RED      = colors.HexColor("#ff4d4d")
    YELLOW   = colors.HexColor("#f0b429")
    TEXT     = colors.HexColor("#e6edf3")
    MUTED    = colors.HexColor("#8b949e")

    W, H = A4
    ML = MR = 18 * mm
    MT = MB = 16 * mm
    INNER_W = W - ML - MR

    buffer = io.BytesIO()

    # ── Background on every page ──────────────────────────────────────────────
    def _bg(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0, 0, W, H, fill=1, stroke=0)
        # header bar
        canvas.setFillColor(SURFACE)
        canvas.rect(0, H - 14 * mm, W, 14 * mm, fill=1, stroke=0)
        canvas.setFillColor(GREEN)
        canvas.rect(0, H - 14 * mm, W, 1, fill=1, stroke=0)
        # header text
        canvas.setFillColor(TEXT)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(ML, H - 9 * mm, "TRADE JOURNAL")
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(W - MR, H - 9 * mm, f"Generated: {report.get('generated_at', '')}")
        # footer
        canvas.setFillColor(SURFACE)
        canvas.rect(0, 0, W, 10 * mm, fill=1, stroke=0)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(W / 2, 3.5 * mm, f"Page {doc.page}  ·  {report['date']}")
        canvas.restoreState()

    frame = Frame(ML, MB + 10 * mm, INNER_W, H - MT - MB - 24 * mm, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(buffer, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=MT + 14 * mm, bottomMargin=MB + 10 * mm)
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_bg)])

    # ── Style helpers ─────────────────────────────────────────────────────────
    def _style(name, **kw) -> ParagraphStyle:
        defaults = dict(fontName="Helvetica", fontSize=10, textColor=TEXT, leading=15, spaceAfter=0)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    S_TITLE   = _style("title",   fontName="Helvetica-Bold", fontSize=22, textColor=TEXT, spaceAfter=2)
    S_SUB     = _style("sub",     fontSize=10, textColor=MUTED)
    S_H2      = _style("h2",      fontName="Helvetica-Bold", fontSize=12, textColor=GREEN, spaceBefore=10, spaceAfter=4)
    S_H3      = _style("h3",      fontName="Helvetica-Bold", fontSize=10, textColor=BLUE,  spaceBefore=6,  spaceAfter=3)
    S_BODY    = _style("body",    fontSize=9,  textColor=TEXT, leading=14)
    S_MUTED   = _style("muted",   fontSize=8,  textColor=MUTED, leading=12)
    S_BULLET  = _style("bullet",  fontSize=9,  textColor=TEXT, leading=14, leftIndent=10)
    S_GREEN   = _style("green",   fontName="Helvetica-Bold", fontSize=14, textColor=GREEN)
    S_RED_BIG = _style("redbig",  fontName="Helvetica-Bold", fontSize=14, textColor=RED)
    S_TAG     = _style("tag",     fontName="Helvetica-Bold", fontSize=7,  textColor=BLUE)

    def p(text, style=None):
        return Paragraph(str(text or "—"), style or S_BODY)

    def sp(h=4):
        return Spacer(1, h * mm)

    def section_header(title: str):
        return [sp(3), p(title.upper(), S_H2), sp(1)]

    def _tbl(data, col_widths, style_cmds):
        base = [
            ("BACKGROUND", (0, 0), (-1, 0), SURFACE),
            ("TEXTCOLOR",  (0, 0), (-1, 0), MUTED),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BG, SURFACE]),
            ("TEXTCOLOR",  (0, 1), (-1, -1), TEXT),
            ("GRID",       (0, 0), (-1, -1), 0.3, BORDER),
            ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ]
        return Table(data, colWidths=col_widths, style=TableStyle(base + style_cmds), hAlign="LEFT")

    # ── Build story ───────────────────────────────────────────────────────────
    story = []
    date  = report["date"]
    daily = report["daily_report"]
    stats = daily["stats"]

    # Cover block
    pnl       = stats["net_pnl"]
    pnl_color = GREEN if pnl >= 0 else RED
    pnl_style = _style("pnl_cov", fontName="Helvetica-Bold", fontSize=32, textColor=pnl_color)
    story += [
        sp(4),
        p(f"Trade Journal Report", S_TITLE),
        p(date, S_SUB),
        sp(6),
        p(f"₹{pnl:+,.2f}", pnl_style),
        p("Net P&L", S_MUTED),
        sp(4),
    ]

    # Summary stat table
    story += section_header("Day Summary")
    sum_data = [
        ["Trades", "Wins", "Losses", "Win Rate", "Best Trade", "Max Drawdown"],
        [
            str(stats["trades"]),
            str(stats["wins"]),
            str(stats["losses"]),
            f"{stats['win_rate']}%",
            f"₹{stats.get('best_trade_pnl') or 0}",
            f"₹{stats.get('max_drawdown') or 0}",
        ],
    ]
    cw = [INNER_W / 6] * 6
    story.append(_tbl(sum_data, cw, [
        ("TEXTCOLOR", (1, 1), (1, 1), GREEN),
        ("TEXTCOLOR", (2, 1), (2, 1), RED),
        ("TEXTCOLOR", (3, 1), (3, 1), BLUE),
        ("FONTNAME",  (0, 1), (-1, 1), "Helvetica-Bold"),
    ]))
    story.append(sp(3))

    # Smart insights
    insights = daily.get("smart_insight", [])
    if insights:
        story += section_header("Smart Insights")
        for insight in insights:
            story.append(p(f"› {insight}", S_BULLET))
            story.append(sp(1))
        story.append(sp(2))

    # Time performance
    story += section_header("Time Performance")
    windows = report.get("time_performance", {}).get("windows", [])
    if windows:
        tw_data = [["Window", "P&L", "Trades", "Wins", "Losses", "Tag"]]
        tw_cmds = []
        for i, w in enumerate(windows, start=1):
            tag = w.get("tag") or ""
            tw_data.append([
                w["label"], f"₹{w['pnl']}", str(w["trades"]),
                str(w["wins"]), str(w["losses"]), tag,
            ])
            pnl_col = GREEN if (w["pnl"] or 0) >= 0 else RED
            tw_cmds.append(("TEXTCOLOR", (1, i), (1, i), pnl_col))
            if tag == "Best":
                tw_cmds.append(("TEXTCOLOR", (5, i), (5, i), GREEN))
            elif tag == "Worst":
                tw_cmds.append(("TEXTCOLOR", (5, i), (5, i), RED))
        cw2 = [INNER_W * f for f in [0.25, 0.17, 0.13, 0.13, 0.13, 0.19]]
        story.append(_tbl(tw_data, cw2, tw_cmds))
        story.append(sp(2))

    pattern = report.get("time_performance", {}).get("pattern_insight")
    if pattern:
        story.append(p(pattern, S_MUTED))
        story.append(sp(2))

    # ── Per-instrument pages ──────────────────────────────────────────────────
    for instrument_item in report.get("instrument_reports", []):
        ir   = instrument_item["report"]
        note = instrument_item.get("note")
        ist  = ir["stats"]
        analysis = ir.get("analysis") or {}
        pnl_val = ir.get("pnl", 0) or 0

        story.append(PageBreak())

        # Instrument header
        inst_pnl_color = GREEN if pnl_val >= 0 else RED
        inst_pnl_style = _style("inst_pnl", fontName="Helvetica-Bold", fontSize=18, textColor=inst_pnl_color)
        story += [
            sp(2),
            p(ir["instrument"], S_TITLE),
            p(f"₹{pnl_val:+,.2f}  ·  {ir.get('direction', '')}  ·  Qty {ir.get('qty', '')}", inst_pnl_style),
            sp(3),
        ]

        # Entry / Exit / Stats table
        entry = ir.get("entry", {})
        exit_ = ir.get("exit", {})
        meta_data = [
            ["Entry Time", "Entry Price", "Exit Time", "Exit Price", "Strategy", "Emotion"],
            [
                entry.get("time", "—"), f"₹{entry.get('price', '—')}",
                exit_.get("time", "—"), f"₹{exit_.get('price', '—')}",
                p(ir.get("strategy") or "—", S_BODY), p(ir.get("emotion") or "—", S_BODY),
            ],
        ]
        cw3 = [INNER_W * f for f in [0.17, 0.17, 0.17, 0.17, 0.18, 0.14]]
        story.append(_tbl(meta_data, cw3, [
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("VALIGN", (4, 1), (5, 1), "TOP"),
        ]))
        story.append(sp(2))

        stat_data = [
            ["Trades", "Wins", "Losses", "Win Rate"],
            [str(ist["trades"]), str(ist["wins"]), str(ist["losses"]), f"{ist['win_rate']}%"],
        ]
        cw4 = [INNER_W / 4] * 4
        story.append(_tbl(stat_data, cw4, [
            ("TEXTCOLOR", (1, 1), (1, 1), GREEN),
            ("TEXTCOLOR", (2, 1), (2, 1), RED),
            ("TEXTCOLOR", (3, 1), (3, 1), BLUE),
            ("FONTNAME",  (0, 1), (-1, 1), "Helvetica-Bold"),
        ]))
        story.append(sp(3))

        # Signal timeline — line-based with badges
        timeline = ir.get("signal_timeline", [])
        if timeline:
            story += section_header("Signal Timeline")

            TAG_COLORS = {"ENTRY": GREEN, "EXIT": RED, "VWAP": BLUE,
                          "ORB": YELLOW, "CAUTION": YELLOW}

            def _badge_cell(tag: str):
                color = TAG_COLORS.get((tag or "").upper(), MUTED)
                label_style = _style(f"bdg_{tag}", fontName="Helvetica-Bold",
                                     fontSize=7, textColor=BG, leading=9)
                inner = Table(
                    [[Paragraph(tag or " ", label_style)]],
                    colWidths=[INNER_W * 0.13],
                    style=TableStyle([
                        ("BACKGROUND",    (0, 0), (-1, -1), color),
                        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
                        ("TOPPADDING",    (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
                    ]),
                )
                return inner

            tl_rows = []
            tl_cmds = []
            for i, ev in enumerate(timeline):
                tag = ev.get("tag") or ""
                title = ev.get("title") or ""
                desc  = ev.get("description") or ""
                content_text = f"<b>{title}</b><br/><font size=8>{desc}</font>"
                is_last = (i == len(timeline) - 1)
                tl_rows.append([
                    p(ev.get("time", ""), S_MUTED),
                    _badge_cell(tag),
                    p(content_text, S_BODY),
                ])
                # Draw continuous vertical line through time column, stop at last row
                if not is_last:
                    tl_cmds.append(("LINEBELOW", (0, i), (0, i), 1, BORDER))

            tl_table = Table(
                tl_rows,
                colWidths=[INNER_W * 0.10, INNER_W * 0.17, INNER_W * 0.73],
                style=TableStyle([
                    ("BACKGROUND",    (0, 0), (-1, -1), BG),
                    ("ROWBACKGROUNDS",(0, 0), (-1, -1), [BG, SURFACE]),
                    ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
                    ("TOPPADDING",    (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LINEAFTER",     (0, 0), (0, -1),  1, BORDER),
                ] + tl_cmds),
            )
            story.append(tl_table)
            story.append(sp(3))

        # Analysis
        analysis_keys = [
            ("why_it_worked",        "Why It Worked"),
            ("what_could_be_better", "What Could Be Better"),
            ("post_exit_summary",    "Post-Exit Summary"),
            ("trade_narrative",      "Trade Narrative"),
        ]
        has_analysis = any(analysis.get(k) for k, _ in analysis_keys)
        if has_analysis:
            story += section_header("Analysis")
            for key, label in analysis_keys:
                val = analysis.get(key)
                if val:
                    story.append(p(label, S_H3))
                    story.append(p(val, S_BODY))
                    story.append(sp(2))

        lessons = analysis.get("lessons") or []
        if lessons:
            story.append(p("Lessons", S_H3))
            for lesson in lessons:
                story.append(p(f"› {lesson}", S_BULLET))
                story.append(sp(1))
            story.append(sp(2))

        # Trades table
        trades = ir.get("trades", [])
        if trades:
            story += section_header("Trades")
            tr_data = [["#", "Direction", "Entry", "Price In", "Exit", "Price Out", "Qty", "P&L"]]
            tr_cmds = []
            for i, t in enumerate(trades, start=1):
                pnl_t = t.get("pnl", 0) or 0
                tr_data.append([
                    str(t.get("trade_no", i)),
                    t.get("direction", ""),
                    t.get("entry_time", ""),
                    f"₹{t.get('entry_price', '')}",
                    t.get("exit_time", ""),
                    f"₹{t.get('exit_price', '')}",
                    str(t.get("qty", "")),
                    f"₹{pnl_t:+,.2f}",
                ])
                pnl_c = GREEN if pnl_t >= 0 else RED
                tr_cmds.append(("TEXTCOLOR", (7, i), (7, i), pnl_c))
            cw6 = [INNER_W * f for f in [0.05, 0.10, 0.10, 0.13, 0.10, 0.13, 0.07, 0.12]]
            # pad to full width with remainder
            cw6[-1] = INNER_W - sum(cw6[:-1])
            story.append(_tbl(tr_data, cw6, tr_cmds))
            story.append(sp(3))

        # Note
        if note and note.get("text"):
            story += section_header("Note")
            story.append(p(note["text"], S_BODY))
            story.append(sp(2))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
