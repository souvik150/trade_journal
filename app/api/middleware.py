from __future__ import annotations

import time

from fastapi import Request

from app.db import mongo_store


async def capture_request_metrics(request: Request, call_next):
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        mongo_store.log_api_request(
            method=request.method,
            path=request.url.path,
            query=str(request.query_params),
            status_code=500,
            duration_ms=(time.perf_counter() - started_at) * 1000,
            error=str(exc),
        )
        raise

    mongo_store.log_api_request(
        method=request.method,
        path=request.url.path,
        query=str(request.query_params),
        status_code=response.status_code,
        duration_ms=(time.perf_counter() - started_at) * 1000,
    )
    return response
