"""Runs of a dataset: the same cases checked against one version of the agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, String

from app.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DatasetRun(Base):
    __tablename__ = "dataset_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dataset_id = Column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(120), nullable=True)
    # What produced the run: commit, model, configuration and the rubric version that scored it.
    version = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, index=True)


class RunResult(Base):
    """The check of one case against the trace the run produced for it."""

    __tablename__ = "run_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(
        String(36), ForeignKey("dataset_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id = Column(String(36), nullable=False, index=True)
    trace_id = Column(String(36), nullable=True)
    status = Column(String(10), nullable=False)  # pass | fail | unknown
    checks = Column(JSON, nullable=False)
    health_score = Column(Float, nullable=True)
    duration_ms = Column(Float, nullable=True)
