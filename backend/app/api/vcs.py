"""CI hook for changes (roadmap step 13b), mounted under ``/v1/vcs``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DbSession
from app.models.vcs import VcsEvent

router = APIRouter()


class VcsEventIn(BaseModel):
    project_id: str = Field("manitos", min_length=1, max_length=128)
    change: str = Field(..., min_length=1, max_length=128, description="pull request number or id")
    event: Literal["merged", "closed"]
    commit: str | None = Field(None, max_length=64)
    branch: str | None = Field(None, max_length=255)
    at: datetime | None = None


@router.post("/events")
async def record_event(body: VcsEventIn, session: AsyncSession = DbSession) -> dict[str, Any]:
    """Record that a change merged or was closed unmerged. Reporting it again changes nothing."""
    existing = (
        await session.execute(
            select(VcsEvent).where(
                VcsEvent.project_id == body.project_id,
                VcsEvent.change == body.change,
                VcsEvent.event == body.event,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"id": existing.id, "duplicate": True}
    row = VcsEvent(
        project_id=body.project_id,
        change=body.change,
        event=body.event,
        commit=body.commit,
        branch=body.branch,
        at=body.at or datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return {"id": row.id, "duplicate": False}
