"""Optional retention for the trace store (audit 2026-10-14: nothing was ever purged).

OBSERVATORY_RETENTION_DAYS (default 0 = keep everything, the previous behaviour) deletes
traces older than that many days with their spans and evaluations, and ingestion receipts of
the same age. It runs at startup and then every OBSERVATORY_RETENTION_INTERVAL_SEC (6 h).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.alerts import AlertEvent
from app.models.evaluation import Evaluation
from app.models.ingestion import IngestionReceipt
from app.models.trace import Span, Trace

logger = logging.getLogger("observatory.retention")


def retention_days() -> int:
    try:
        return max(0, int(os.getenv("OBSERVATORY_RETENTION_DAYS", "0")))
    except ValueError:
        return 0


def retention_interval_sec() -> float:
    try:
        return max(60.0, float(os.getenv("OBSERVATORY_RETENTION_INTERVAL_SEC", str(6 * 3600))))
    except ValueError:
        return 6 * 3600.0


async def purge_older_than(
    session: AsyncSession, days: int, *, now: datetime | None = None
) -> dict[str, int]:
    """Delete what is older than ``days``; children first (SQLite foreign keys on or off)."""
    if days <= 0:
        return {"traces": 0, "spans": 0, "evaluations": 0, "receipts": 0, "alert_events": 0}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    old_traces = select(Trace.id).where(Trace.created_at < cutoff)
    evaluations = await session.execute(
        delete(Evaluation).where(Evaluation.trace_id.in_(old_traces))
    )
    spans = await session.execute(delete(Span).where(Span.trace_id.in_(old_traces)))
    traces = await session.execute(delete(Trace).where(Trace.created_at < cutoff))
    receipts = await session.execute(
        delete(IngestionReceipt).where(IngestionReceipt.created_at < cutoff)
    )
    alert_events = await session.execute(delete(AlertEvent).where(AlertEvent.fired_at < cutoff))
    await session.commit()
    return {
        "alert_events": alert_events.rowcount or 0,
        "traces": traces.rowcount or 0,
        "spans": spans.rowcount or 0,
        "evaluations": evaluations.rowcount or 0,
        "receipts": receipts.rowcount or 0,
    }


async def retention_loop(engine: AsyncEngine) -> None:
    days = retention_days()
    if days <= 0:
        return
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    while True:
        try:
            async with factory() as session:
                counts = await purge_older_than(session, days)
            if any(counts.values()):
                logger.info("retention: purged %s (older than %d days)", counts, days)
        except Exception:
            logger.warning("retention pass failed", exc_info=True)
        await asyncio.sleep(retention_interval_sec())
