"""Alert rules and the events they raise - compatible with both PostgreSQL and SQLite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String

from app.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AlertRule(Base):
    """A threshold on a metric over a sliding window (e.g. p95 latency > 30 s over 60 min)."""

    __tablename__ = "alert_rules"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(120), nullable=False)
    metric = Column(String(40), nullable=False)
    comparator = Column(String(2), nullable=False, default=">")
    threshold = Column(Float, nullable=False)
    window_minutes = Column(Integer, nullable=False, default=60)
    min_samples = Column(Integer, nullable=False, default=5)
    scope = Column(String(128), nullable=True)  # provider id for provider metrics
    project_id = Column(String(128), nullable=False, default="manitos")
    environment = Column(String(64), nullable=True)
    severity = Column(String(10), nullable=False, default="warning")
    enabled = Column(Boolean, nullable=False, default=True)
    cooldown_minutes = Column(Integer, nullable=False, default=30)
    created_at = Column(DateTime(timezone=True), default=_now)


class AlertEvent(Base):
    """One firing of a rule. Self-contained, so history survives later edits of the rule."""

    __tablename__ = "alert_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rule_id = Column(
        String(36),
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_name = Column(String(120), nullable=False)
    metric = Column(String(40), nullable=False)
    scope = Column(String(128), nullable=True)
    comparator = Column(String(2), nullable=False)
    severity = Column(String(10), nullable=False)
    state = Column(String(10), nullable=False, default="open", index=True)
    value = Column(Float, nullable=True)
    threshold = Column(Float, nullable=False)
    window_minutes = Column(Integer, nullable=False)
    fired_at = Column(DateTime(timezone=True), default=_now, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    acked_at = Column(DateTime(timezone=True), nullable=True)
    details = Column(JSON, nullable=True)
