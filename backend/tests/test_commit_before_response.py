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
