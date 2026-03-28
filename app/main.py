from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn

from app.api.middleware import capture_request_metrics
from app.api.routes.analytics import router as analytics_router
from app.api.routes.journal import router as journal_router
from app.api.routes.monitoring import router as monitoring_router
from app.api.routes.notes import router as notes_router
from app.api.routes.trade import router as trade_router
from app.services.runtime import bootstrap_data


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap_data()
    logger.info("Trade Journal AI running on http://0.0.0.0:%s", os.getenv("PORT", "8000"))
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="Trade Journal AI", version="0.2.0", lifespan=lifespan)
    application.middleware("http")(capture_request_metrics)
    application.include_router(journal_router)
    application.include_router(analytics_router)
    application.include_router(trade_router)
    application.include_router(notes_router)
    application.include_router(monitoring_router)
    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
        log_level="info",
        access_log=True,
    )
