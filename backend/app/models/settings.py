"""Per-project settings (capture mode) and configuration sections - SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, String

from app.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectSetting(Base):
    __tablename__ = "project_settings"

    project_id = Column(String(128), primary_key=True)
    capture_mode = Column(String(10), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now)


class ProjectConfig(Base):
    """One section of a project's configuration (``groundedness``, later ``cost``, ``budgets``).

    Kept apart from ``project_settings``: its ``capture_mode`` is NOT NULL and the privacy DELETE
    removes the whole row, so a section stored there would vanish with a capture-mode reset.
    """

    __tablename__ = "project_configs"

    project_id = Column(String(128), primary_key=True)
    section = Column(String(40), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
