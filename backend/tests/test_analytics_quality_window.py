"""Fixed-interval ``since``/``until`` aggregation for the quality endpoint."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest

from app.models import Span, Trace


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _insert_trace(db_session, *, name: str, created_at: datetime, status: str = "ok"):
    trace = Trace(
        name=name,
        start_time=created_at,
        status=status,
        project_id="manitos",
        environment="test",
        created_at=created_at,
    )
    db_session.add(trace)
    await db_session.flush()
    return trace


@pytest.mark.anyio
async def test_manitos_quality_fixed_window_counts_and_bounds(client, db_session):
    now = _now()
    await _insert_trace(db_session, name="old", created_at=now - timedelta(hours=2), status="error")
    await _insert_trace(db_session, name="mid", created_at=now - timedelta(minutes=45))
    new = await _insert_trace(db_session, name="new", created_at=now - timedelta(minutes=5))
    db_session.add(
        Span(
            trace_id=new.id,
            name="manitos.turn.lifecycle",
            span_type="llm",
            start_time=now - timedelta(minutes=5),
            status="ok",
            attributes={"duration_ms": 500, "llm_degraded": True},
        )
    )
    await db_session.commit()

    since = (now - timedelta(hours=1)).isoformat()
    response = await client.get(f"/v1/analytics/manitos-quality?{urlencode({'since': since})}")
    assert response.status_code == 200
    data = response.json()
    assert data["total_turns"] == 2
    assert data["error_count"] == 0
    assert data["avg_duration_ms"] == 500
    assert data["degraded_count"] == 1
    assert data["window_start"] is not None
    assert data["window_end"] is not None

    until = (now - timedelta(minutes=10)).isoformat()
    response = await client.get(
        f"/v1/analytics/manitos-quality?{urlencode({'since': since, 'until': until})}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_turns"] == 1
    assert data["avg_duration_ms"] == 0


@pytest.mark.anyio
async def test_manitos_quality_without_since_keeps_rolling_window(client, db_session):
    now = _now()
    await _insert_trace(db_session, name="old", created_at=now - timedelta(hours=2))
    await _insert_trace(
        db_session, name="mid", created_at=now - timedelta(minutes=45), status="error"
    )
    await _insert_trace(db_session, name="new", created_at=now - timedelta(minutes=5))
    await db_session.commit()

    response = await client.get("/v1/analytics/manitos-quality")
    assert response.status_code == 200
    data = response.json()
    assert data["total_turns"] == 3
    assert data["error_count"] == 1
    assert data["window_start"] is not None
    assert data["window_end"] is not None
