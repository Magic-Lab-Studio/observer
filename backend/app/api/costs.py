"""Cost per outcome (roadmap step 13a), mounted under ``/v1/costs``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import cost_outcomes, vcs_costs
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


@router.get("/changes")
async def cost_per_change(
    project_id: str = Query("manitos", min_length=1, max_length=128),
    hours: float = Query(720.0, gt=0, le=2160),
    session: AsyncSession = DbSession,
) -> dict[str, Any]:
    """Cost per merged change: spend on merged and abandoned changes over the merged count; spend on
    open changes and spend with no change reported apart (step 13b)."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return await vcs_costs.summary(session, project_id=project_id, since=since)
