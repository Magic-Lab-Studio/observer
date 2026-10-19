"""Is telemetry still arriving? (``GET /v1/ingest/health``)

Per project: when the last trace was stored and how many arrived in the last 5 minutes, hour and
day. A sender that stops (crashed, blocked, spool full) shows up as growing silence, which the
``ingest_silence_minutes`` alert metric turns into a notification.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DbSession
from app.models.trace import Trace

router = APIRouter()


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def silence_minutes(
    session: AsyncSession, project_id: str, environment: str | None, now: datetime
) -> float | None:
    """Minutes since the project's last stored trace; ``None`` if it never sent one."""
    query = select(func.max(Trace.created_at)).where(Trace.project_id == project_id)
    if environment:
        query = query.where(Trace.environment == environment)
    last = _aware((await session.execute(query)).scalar_one_or_none())
    return None if last is None else max(0.0, (now - last).total_seconds() / 60.0)


@router.get("/health")
async def ingest_health(session: AsyncSession = DbSession) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    t5, t60, t24 = (now - timedelta(minutes=5), now - timedelta(hours=1), now - timedelta(days=1))
    rows = (
        await session.execute(
            select(
                Trace.project_id,
                func.max(Trace.created_at),
                func.count().filter(Trace.created_at >= t5),
                func.count().filter(Trace.created_at >= t60),
                func.count().filter(Trace.created_at >= t24),
            )
            .where(Trace.project_id.is_not(None))
            .group_by(Trace.project_id)
        )
    ).all()
    projects = []
    for project_id, last, n5, n60, n24 in rows:
        last = _aware(last)
        projects.append(
            {
                "project_id": project_id,
                "last_trace_at": last.isoformat() if last else None,
                "silence_minutes": round((now - last).total_seconds() / 60.0, 1) if last else None,
                "traces_5m": n5,
                "traces_1h": n60,
                "traces_24h": n24,
            }
        )
    projects.sort(key=lambda p: p["silence_minutes"] if p["silence_minutes"] is not None else 1e9)
    return {"checked_at": now.isoformat(), "projects": projects}
