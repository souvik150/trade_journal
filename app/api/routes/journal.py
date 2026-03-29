from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response

from app.integrations.whatsapp import send_file
from app.services.export_service import render_daily_report_csv, render_daily_report_pdf
from app.services.trade_journal import (
    build_daily_download_report,
    get_monthly_journal,
    get_or_build_daily_pnl_flow,
    get_or_build_daily_report,
    get_or_build_yearly_pnl,
)


router = APIRouter(tags=["journal"])


@router.get("/journal/monthly")
def journal_monthly(year: int = Query(...), month: int = Query(...)):
    return get_monthly_journal(year, month)


@router.get("/journal/daily")
def journal_daily(date: str = Query(...)):
    report = get_or_build_daily_report(date)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No daily report available for {date}.")
    return report


@router.get("/journal/daily/export")
def journal_daily_export(date: str = Query(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    report = get_or_build_daily_report(date)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No daily report available for {date}.")
    csv_text = render_daily_report_csv(report)
    filename = f"trade_journal_daily_{date}.csv"
    background_tasks.add_task(
        send_file,
        data=csv_text.encode(),
        mime_type="text/csv",
        filename=filename,
        caption=f"Trade Journal CSV — {date}",
    )
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=background_tasks,
    )


@router.get("/journal/daily/report")
def journal_daily_report_download(date: str = Query(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    report = build_daily_download_report(date)
    pdf_bytes = render_daily_report_pdf(report)
    filename = f"trade_journal_report_{date}.pdf"
    background_tasks.add_task(
        send_file,
        data=pdf_bytes,
        mime_type="application/pdf",
        filename=filename,
        caption=f"Trade Journal Report — {date}",
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=background_tasks,
    )


@router.get("/journal/yearly")
def journal_yearly(year: int = Query(...)):
    return get_or_build_yearly_pnl(year)


@router.get("/journal/daily/pnl-flow")
def journal_pnl_flow(date: str = Query(...)):
    report = get_or_build_daily_pnl_flow(date)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No orders available for {date}.")
    return report
