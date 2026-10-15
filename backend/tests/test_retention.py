"""OBSERVATORY_RETENTION_DAYS purges old traces, their spans and receipts (audit 2026-10-14)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import Span, Trace
from app.retention import purge_older_than, retention_days


async def _trace(db_session, created_at):
    trace = Trace(
        name="t",
        start_time=created_at,
        status="ok",
        project_id="manitos",
        environment="test",
        created_at=created_at,
    )
    db_session.add(trace)
    await db_session.flush()
    db_session.add(
        Span(
            trace_id=trace.id,
            name="s",
            span_type="llm",
            start_time=created_at,
            status="ok",
            attributes={},
        )
    )
    return trace


@pytest.mark.anyio
async def test_purge_removes_only_what_is_older_than_the_window(db_session):
    now = datetime.now(timezone.utc)
    await _trace(db_session, now - timedelta(days=40))
    keep = await _trace(db_session, now - timedelta(days=2))
    await db_session.commit()

    counts = await purge_older_than(db_session, 30, now=now)

    assert counts["traces"] == 1 and counts["spans"] == 1
    remaining = (await db_session.execute(select(Trace.id))).scalars().all()
    assert remaining == [keep.id]
    assert (await db_session.execute(select(func.count()).select_from(Span))).scalar() == 1


@pytest.mark.anyio
async def test_zero_days_keeps_everything(db_session, monkeypatch):
    monkeypatch.delenv("OBSERVATORY_RETENTION_DAYS", raising=False)
    assert retention_days() == 0
    await _trace(db_session, datetime.now(timezone.utc) - timedelta(days=400))
    await db_session.commit()
    assert (await purge_older_than(db_session, 0))["traces"] == 0
    assert (await db_session.execute(select(func.count()).select_from(Trace))).scalar() == 1
