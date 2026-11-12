"""Cost per outcome (roadmap step 13a), mounted under ``/v1/costs``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import cost_outcomes
from app.db import DbSession

router = APIRouter()


@router.get("/summary")
async def cost_summary(
    project_id: str | None = Query(None, max_length=128),
    environment: str | None = Query(None, max_length=64),
    hours: float = Query(168.0, gt=0, le=2160),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    """Spend of the window by bucket (useful, failed, error, repeated, unknown), per answered
    turn, per user (top 5%) and per model."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return await cost_outcomes.summary(
        session, project_id=project_id, environment=environment, since=since
    )
