"""An acknowledged write is durable: the session commits before the response is sent."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import get_session
from app.main import app


@pytest.mark.anyio
async def test_a_failed_commit_is_an_error_for_the_client_not_a_200(db_engine):
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def failing_commit():
        async with factory() as session:
            yield session
            raise RuntimeError("disk full")  # where the commit would run

    app.dependency_overrides[get_session] = failing_commit
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            span = {"trace_id": str(uuid.uuid4()), "name": "a", "start_time": 1_760_000_000.0,
                    "end_time": 1_760_000_001.0, "status": "ok"}
            response = await client.post("/v1/traces/batch", json={"spans": [span]})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500


def test_every_route_commits_before_responding():
    from pathlib import Path

    sources = Path(__file__).resolve().parents[1].joinpath("app").rglob("*.py")
    loose = [p.name for p in sources if "Depends(get_session)" in p.read_text()]
    assert loose == []


@pytest.mark.anyio
async def test_the_sdk_exporter_retries_a_failed_commit_and_stores_the_span_once(db_engine):
    """A failed commit now reaches the exporter as a 500, which it retries; the retry and a
    later resend of the same batch are idempotent."""
    sdk = pytest.importorskip("llm_observatory")
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from app.models.trace import Span

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    calls = {"n": 0}

    async def flaky_commit():
        async with factory() as session:
            yield session
            calls["n"] += 1
            if calls["n"] == 1:
                await session.rollback()
                raise RuntimeError("database is locked")
            await session.commit()

    app.dependency_overrides[get_session] = flaky_commit
    try:
        with TestClient(app, raise_server_exceptions=False) as http:
            import httpx as sdk_httpx  # the library the exporter itself uses

            def bridge(request):
                answer = http.request(request.method, str(request.url), content=request.content,
                                      headers=dict(request.headers))
                return sdk_httpx.Response(answer.status_code, content=answer.content)

            exporter = sdk.OTLPExporter(endpoint="http://testserver", max_retries=3,
                                        sleep=lambda _s: None)
            exporter._client = sdk_httpx.Client(transport=sdk_httpx.MockTransport(bridge))
            span = {"id": str(uuid.uuid4()), "trace_id": str(uuid.uuid4()), "name": "llm",
                    "start_time": 1_760_000_000.0, "end_time": 1_760_000_001.0, "status": "ok"}
            exporter.export([span])
            exporter.export([span])  # the same batch again, as a spool replay would
            stats = exporter.stats()
    finally:
        app.dependency_overrides.clear()
    assert stats["retried"] == 1 and stats["exported"] == 2 and stats["dropped"] == 0
    async with factory() as session:
        stored = await session.scalar(select(func.count()).select_from(Span))
    assert stored == 1
