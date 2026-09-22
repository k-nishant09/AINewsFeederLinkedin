"""FastAPI application entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from daily_news.api.routes.approval import router as approval_router
from daily_news.api.routes.news import router as news_router
from daily_news.api.routes.workflow import router as workflow_router
from daily_news.config.settings import get_settings
from daily_news.observability.tracing import setup_tracing

# Configure logging early — before any logger is used.
# Uvicorn sets up its own handlers but leaves the root logger at WARNING,
# so application-level INFO logs are silently dropped unless we configure here.
_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Keep noisy third-party loggers quiet
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("opentelemetry").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_tracing()
        logger.info("AI Daily News Platform started (env=%s)", settings.app_env)
        yield

    app = FastAPI(
        title="AI Daily News Platform",
        description=(
            "Autonomous AI Daily News Multi-Agent Platform — "
            "LangGraph + LangChain + MCP + FastAPI"
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url="/redoc" if settings.app_env != "production" else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(news_router, prefix="/news", tags=["news"])
    app.include_router(workflow_router, prefix="/workflow", tags=["workflow"])
    app.include_router(approval_router, prefix="/approval", tags=["approval"])

    # ── Health / Ready ────────────────────────────────────────────────────────
    @app.get("/health", tags=["observability"])
    async def health():
        return {"status": "healthy"}

    @app.get("/ready", tags=["observability"])
    async def ready():
        return {"status": "ready"}

    return app


app = create_app()


def start():
    uvicorn.run("daily_news.api.main:app", host="0.0.0.0", port=8000, reload=False)
