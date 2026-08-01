"""FastAPI application for LLM Observatory."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api import analytics, evaluations, manitos_ingest, traces
from app.auth import require_api_key
from app.db.postgres import engine
from app.ratelimit import limiter
from app.websocket import manager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    yield
    await engine.dispose()


app = FastAPI(
    title="LLM Observatory",
    description="Open-source observability and telemetry platform for LLMs",
    version="0.1.1",
    lifespan=lifespan,
)

# Rate limiter
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded",
            "retry_after": exc.detail,
        },
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]

# CORS middleware - configurable via CORS_ORIGINS env var (comma-separated)
_cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers with authentication
_app_auth = Depends(require_api_key)
app.include_router(traces.router, prefix="/v1/traces", tags=["traces"], dependencies=[_app_auth])
app.include_router(
    evaluations.router,
    prefix="/v1/evaluations",
    tags=["evaluations"],
    dependencies=[_app_auth],
)
app.include_router(
    analytics.router,
    prefix="/v1/analytics",
    tags=["analytics"],
    dependencies=[_app_auth],
)
app.include_router(
    manitos_ingest.router,
    prefix="/v1/ingest/manitos",
    tags=["manitos-ingest"],
    dependencies=[_app_auth],
)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint with database status."""
    from sqlalchemy import text

    from app.db import async_session

    db_status = "ok"
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    return {"status": "healthy", "db": db_status, "version": "0.1.1"}


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "name": "LLM Observatory",
        "version": "0.1.1",
        "docs": "/docs",
    }


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time trace streaming."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.send_personal(websocket, {"type": "pong", "data": data})
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
