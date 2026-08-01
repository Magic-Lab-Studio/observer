"""Trace and Span models - compatible with both PostgreSQL and SQLite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.models import Base


class Trace(Base):
    """Represents a complete trace (collection of spans)."""

    __tablename__ = "traces"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    # Integration session identifiers are stable opaque strings, not
    # necessarily UUIDs. Keep trace/span IDs UUID-sized without discarding
    # source-system correlation identifiers.
    session_id = Column(String(255), nullable=True, index=True)
    turn_id = Column(String(128), nullable=True, index=True)
    project_id = Column(String(128), nullable=True, index=True)
    environment = Column(String(64), nullable=True)
    service_instance_id = Column(String(128), nullable=True)
    actor_id_hash = Column(String(128), nullable=True)
    schema_version = Column(String(64), nullable=False, default="observer.trace.v1")
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="unset")
    meta = Column("metadata", JSON, nullable=True, key="meta")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    spans = relationship("Span", back_populates="trace", cascade="all, delete-orphan")


class Span(Base):
    """Represents a single span within a trace."""

    __tablename__ = "spans"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    trace_id = Column(
        String(36), ForeignKey("traces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_span_id = Column(String(36), ForeignKey("spans.id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    span_type = Column(String(50), nullable=False, default="generic")
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="unset")
    inp = Column("input", JSON, nullable=True, key="inp")
    out = Column("output", JSON, nullable=True, key="out")
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(10, 6), nullable=True)
    meta = Column("metadata", JSON, nullable=True, key="meta")
    attributes = Column(JSON, nullable=True)

    # Relationships
    trace = relationship("Trace", back_populates="spans")
    parent = relationship("Span", remote_side=[id], backref="children")
