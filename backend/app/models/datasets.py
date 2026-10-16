"""Test-case datasets built from traces - metadata only, compatible with SQLite and PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Text

from app.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(120), nullable=False)
    project_id = Column(String(128), nullable=False, default="manitos", index=True)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class DatasetItem(Base):
    """One saved case. Never holds text copied from a trace: ``observed`` is metadata and
    ``expect`` is written by a person. ``source_trace_id`` is not a foreign key, so cases outlive
    the retention purge of their trace."""

    __tablename__ = "dataset_items"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id = Column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_trace_id = Column(String(36), nullable=True)
    intent = Column(String(80), nullable=True)
    tags = Column(JSON, nullable=True)
    expect = Column(JSON, nullable=False)
    label = Column(String(10), nullable=True)  # human verdict: "correct" | "incorrect"
    notes = Column(Text, nullable=True)
    observed = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
