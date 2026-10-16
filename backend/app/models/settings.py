"""Per-project settings (capture mode) - compatible with SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String

from app.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectSetting(Base):
    __tablename__ = "project_settings"

    project_id = Column(String(128), primary_key=True)
    capture_mode = Column(String(10), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now)
