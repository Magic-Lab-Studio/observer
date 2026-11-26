"""What happened to a change (pull request) after the agent worked on it: merged or closed unmerged.

A CI hook reports it (``POST /v1/vcs/events``); traces name the change in ``vcs.change`` (or the
commit in ``vcs.commit``), so the spend on a change can be set against whether it shipped (step 13).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, UniqueConstraint

from app.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VcsEvent(Base):
    __tablename__ = "vcs_events"
    __table_args__ = (UniqueConstraint("project_id", "change", "event", name="uq_vcs_event"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(128), nullable=False, index=True)
    change = Column(String(128), nullable=False)  # a pull request number or id
    event = Column(String(16), nullable=False)  # "merged" | "closed" (closed without merging)
    commit = Column(String(64), nullable=True, index=True)  # the merge commit, or the head
    branch = Column(String(255), nullable=True)
    at = Column(DateTime(timezone=True), nullable=False, default=_now)
    created_at = Column(DateTime(timezone=True), default=_now)
