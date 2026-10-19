"""Trace API endpoints."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.capture import apply_capture, capture_mode
from app.db import get_session
from app.ingest_validation import (
    OpaqueId,
    OptionalTimestamp,
    SpanStatusValue,
    Timestamp,
    span_storage_id,
    to_datetime,
    trace_lookup_id,
    trace_storage_id,
)
from app.limits import (
    MAX_SPANS_PER_BATCH,
    MAX_SPANS_PER_TRACE,
    MAX_TRACES_PER_IMPORT,
    bounded_json,
)
from app.loop_detection import min_repeats, repeated_calls
from app.models.evaluation import Evaluation
from app.models.trace import Span, Trace
from app.ratelimit import limiter
from app.trace_summary import fill_trace_identity, refresh_trace_summary
from app.websocket import manager

logger = logging.getLogger(__name__)
router = APIRouter()

# The SDK flushes every few seconds, so the old fixed 10/minute rejected healthy senders.
_BATCH_LIMIT = os.getenv("RATE_LIMIT_BATCH", "120/minute")


async def _resolve_trace_id(session: AsyncSession, raw: str) -> str:
    """The stored ID for a path's trace ID: the mapped ID, or the raw value for a trace stored
    under a non-UUID ID before IDs were mapped. 400 when the value cannot be an ID."""
    mapped = trace_lookup_id(raw)
    if mapped is None:
        raise HTTPException(status_code=400, detail="Invalid trace ID format")
    if raw != mapped and await session.get(Trace, mapped) is None:
        if await session.get(Trace, raw) is not None:
            return raw
    return mapped


_safe_uuid = trace_lookup_id


def _check_interval(start: float | None, end: float | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("end_time cannot precede start_time")


class SpanCreate(BaseModel):
    """Schema for creating a span (see ``app.ingest_validation`` for the field rules)."""

    id: OpaqueId = None
    trace_id: OpaqueId = None
    parent_id: OpaqueId = None
    name: str = Field(min_length=1, max_length=255)
    span_type: str = Field("generic", min_length=1, max_length=50)
    start_time: Timestamp
    end_time: OptionalTimestamp = None
    status: SpanStatusValue = "unset"
    input: dict | None = None
    output: dict | None = None
    tokens_input: int | None = Field(None, ge=0)
    tokens_output: int | None = Field(None, ge=0)
    cost_usd: float | None = Field(None, ge=0, allow_inf_nan=False)
    metadata: dict | None = None
    attributes: dict | None = None

    @field_validator("input", "output", "metadata", "attributes")
    @classmethod
    def _bounded(cls, value: dict | None) -> dict | None:
        return bounded_json(value)

    @model_validator(mode="after")
    def _interval(self) -> SpanCreate:
        _check_interval(self.start_time, self.end_time)
        if self.trace_id is not None:
            self.trace_id = trace_storage_id(self.trace_id)
        if self.id is not None:
            self.id = span_storage_id(self.id, self.trace_id)
        if self.parent_id is not None:
            self.parent_id = span_storage_id(self.parent_id, self.trace_id)
            if self.parent_id == self.id:
                raise ValueError("a span cannot be its own parent")
        return self


class TraceCreate(BaseModel):
    """Schema for creating a trace."""

    name: str = Field(min_length=1, max_length=255)
    session_id: str | None = None
    start_time: Timestamp
    end_time: OptionalTimestamp = None
    status: SpanStatusValue = "unset"
    metadata: dict | None = None
    spans: list[SpanCreate] = Field(default_factory=list, max_length=MAX_SPANS_PER_TRACE)

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, value: dict | None) -> dict | None:
        return bounded_json(value)

    @model_validator(mode="after")
    def _interval(self) -> TraceCreate:
        _check_interval(self.start_time, self.end_time)
        return self

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip()
        if not normalized or len(normalized) > 255 or any(ord(char) < 32 for char in normalized):
            raise ValueError("session_id must be 1-255 characters without control characters")
        return normalized


class BatchSpansCreate(BaseModel):
    """Schema for batch-creating spans (auto-creates traces)."""

    spans: list[SpanCreate] = Field(default_factory=list, max_length=MAX_SPANS_PER_BATCH)


class TraceResponse(BaseModel):
    """Schema for trace response."""

    model_config = {"from_attributes": True}

    id: str
    name: str
    session_id: str | None
    turn_id: str | None
    project_id: str | None
    environment: str | None
    service_instance_id: str | None
    actor_id_hash: str | None
    schema_version: str
    start_time: datetime
    end_time: datetime | None
    status: str
    metadata: dict | None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _map_orm_keys(cls, values: dict | object) -> dict:
        if hasattr(values, "meta"):
            return {
                "id": values.id,
                "name": values.name,
                "session_id": values.session_id,
                "turn_id": values.turn_id,
                "project_id": values.project_id,
                "environment": values.environment,
                "service_instance_id": values.service_instance_id,
                "actor_id_hash": values.actor_id_hash,
                "schema_version": values.schema_version,
                "start_time": values.start_time,
                "end_time": values.end_time,
                "status": values.status,
                "metadata": values.meta,
                "created_at": values.created_at,
            }
        return values


class TraceListResponse(BaseModel):
    """Paginated trace list response."""

    traces: list[TraceResponse]
    total: int
    limit: int
    offset: int


class SpanResponse(BaseModel):
    """Schema for span response."""

    model_config = {"from_attributes": True}

    id: str
    trace_id: str
    parent_id: str | None
    name: str
    span_type: str
    start_time: datetime
    end_time: datetime | None
    status: str
    input: dict | None
    output: dict | None
    tokens_input: int | None
    tokens_output: int | None
    cost_usd: float | None
    metadata: dict | None
    attributes: dict | None

    @model_validator(mode="before")
    @classmethod
    def _map_orm_keys(cls, values: dict | object) -> dict:
        if hasattr(values, "inp"):
            return {
                "id": values.id,
                "trace_id": values.trace_id,
                "parent_id": values.parent_span_id,
                "name": values.name,
                "span_type": values.span_type,
                "start_time": values.start_time,
                "end_time": values.end_time,
                "status": values.status,
                "input": values.inp,
                "output": values.out,
                "tokens_input": values.tokens_input,
                "tokens_output": values.tokens_output,
                "cost_usd": float(values.cost_usd) if values.cost_usd else None,
                "metadata": values.meta,
                "attributes": values.attributes,
            }
        return values


@router.post("/", response_model=TraceResponse)
async def create_trace(
    trace_data: TraceCreate,
    session: AsyncSession = Depends(get_session),
) -> Trace:
    """Create a new trace with optional spans."""
    trace_id = str(uuid.uuid4())
    trace = Trace(
        id=trace_id,
        name=trace_data.name,
        session_id=trace_data.session_id,
        start_time=to_datetime(trace_data.start_time),
        end_time=to_datetime(trace_data.end_time),
        status=trace_data.status,
        meta=trace_data.metadata,
    )
    session.add(trace)

    # Add spans
    mode = await capture_mode(session, trace.project_id, "full")
    for span_data in trace_data.spans:
        span_end = to_datetime(span_data.end_time)
        captured = apply_capture(
            mode,
            inp=span_data.input,
            out=span_data.output,
            attributes=span_data.attributes,
            meta=span_data.metadata,
        )
        span = Span(
            id=span_data.id or str(uuid.uuid4()),
            trace_id=trace_id,
            parent_span_id=span_data.parent_id,
            name=span_data.name,
            span_type=span_data.span_type,
            start_time=to_datetime(span_data.start_time),
            end_time=span_end,
            status=span_data.status,
            inp=captured[0],
            out=captured[1],
            tokens_input=span_data.tokens_input,
            tokens_output=span_data.tokens_output,
            cost_usd=span_data.cost_usd,
            meta=captured[3],
            attributes=captured[2],
        )
        session.add(span)

    await session.flush()

    # Broadcast new trace via WebSocket
    await manager.broadcast({
        "type": "new_trace",
        "data": {
            "id": trace_id,
            "name": trace.name,
            "status": trace.status,
            "created_at": trace.created_at.isoformat() if trace.created_at else None,
        },
    })

    return trace


def _span_values(span_data: SpanCreate, mode: str) -> dict[str, Any]:
    inp, out, attributes, meta = apply_capture(
        mode,
        inp=span_data.input,
        out=span_data.output,
        attributes=span_data.attributes,
        meta=span_data.metadata,
    )
    return {
        "parent_span_id": span_data.parent_id,
        "name": span_data.name,
        "span_type": span_data.span_type,
        "start_time": to_datetime(span_data.start_time),
        "end_time": to_datetime(span_data.end_time),
        "status": span_data.status,
        "inp": inp,
        "out": out,
        "tokens_input": span_data.tokens_input,
        "tokens_output": span_data.tokens_output,
        "cost_usd": span_data.cost_usd,
        "meta": meta,
        "attributes": attributes,
    }


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return abs(left.timestamp() - right.timestamp()) < 0.000001


def _same_span(existing: Span, values: dict[str, Any]) -> bool:
    for key, value in values.items():
        current = getattr(existing, key)
        if key in {"start_time", "end_time"}:
            if not _same_instant(current, value):
                return False
        elif key == "cost_usd":
            if (round(float(current), 6) if current is not None else None) != (
                round(value, 6) if value is not None else None
            ):
                return False
        elif current != value:
            return False
    return True


async def _ingest_trace_group(
    session: AsyncSession, trace_id: str, spans: list[SpanCreate], counts: dict[str, int]
) -> Trace:
    """Store one trace's spans. A stored span that has not ended yet is completed by a newer
    version; a finished span is never overwritten (a retry of the same data is a duplicate, a
    different version a conflict). Capture mode applies to every write, updates included."""
    trace = await session.get(Trace, trace_id)
    if trace is None:
        first = min(spans, key=lambda s: s.start_time)
        trace = Trace(
            id=trace_id,
            name=first.name,
            start_time=to_datetime(first.start_time),
            status="unset",
        )
        session.add(trace)
    # before the capture mode is read: the project decides it
    fill_trace_identity(trace, [s.attributes for s in spans])
    await session.flush()

    mode = await capture_mode(session, trace.project_id, "full")
    ids = [s.id for s in spans if s.id]
    stored: dict[str, Span] = {}
    if ids:
        rows = await session.execute(select(Span).where(Span.id.in_(ids)))
        stored = {str(span.id): span for span in rows.scalars()}

    for span_data in spans:
        values = _span_values(span_data, mode)
        existing = stored.get(span_data.id) if span_data.id else None
        if existing is None:
            span = Span(id=span_data.id or str(uuid.uuid4()), trace_id=trace_id, **values)
            session.add(span)
            if span_data.id:
                stored[span_data.id] = span
            counts["accepted"] += 1
        elif str(existing.trace_id) != trace_id:
            counts["conflict"] += 1
            logger.warning("span %s belongs to another trace; not stored", existing.id)
        elif _same_span(existing, values):
            counts["duplicate"] += 1
        elif existing.end_time is None:
            for key, value in values.items():
                setattr(existing, key, value)
            counts["updated"] += 1
        else:
            counts["conflict"] += 1
            logger.info("span %s is already finished; a different version was ignored", existing.id)

    await refresh_trace_summary(session, trace)
    return trace


@router.post("/batch", response_model=list[TraceResponse])
@limiter.limit(_BATCH_LIMIT)
async def create_traces_batch(
    request: Request,
    response: Response,
    batch_data: BatchSpansCreate,
    session: AsyncSession = Depends(get_session),
) -> list[Trace]:
    """Create or extend traces from a batch of spans (the SDK exporters' endpoint).

    Spans are grouped by ``trace_id`` (a span without one starts a new trace). Retries are safe:
    a span already stored with the same data is a duplicate. A span stored before it finished is
    completed by the finished version; a finished span is never overwritten. After each batch the
    trace's status, interval and name are derived from all of its stored spans
    (``app.trace_summary``). The ``X-Observer-Spans`` header reports
    ``accepted``, ``updated``, ``duplicate`` and ``conflict`` counts.
    """
    spans_by_trace: dict[str, list[SpanCreate]] = {}
    for span_data in batch_data.spans:
        trace_id = span_data.trace_id or str(uuid.uuid4())
        spans_by_trace.setdefault(trace_id, []).append(span_data)

    counts = dict.fromkeys(("accepted", "updated", "duplicate", "conflict"), 0)
    traces: list[Trace] = []
    for trace_id, spans in spans_by_trace.items():
        before = dict(counts)
        try:
            async with session.begin_nested():
                traces.append(await _ingest_trace_group(session, trace_id, spans, counts))
        except IntegrityError:
            # A concurrent request stored the same trace or span first: read what it stored.
            counts.update(before)
            async with session.begin_nested():
                traces.append(await _ingest_trace_group(session, trace_id, spans, counts))

    await session.flush()
    response.headers["X-Observer-Spans"] = ",".join(f"{k}={v}" for k, v in counts.items())

    for trace in traces:
        await manager.broadcast({
            "type": "new_trace",
            "data": {
                "id": str(trace.id),
                "name": trace.name,
                "status": trace.status,
                "created_at": trace.created_at.isoformat() if trace.created_at else None,
            },
        })

    return traces


@router.get("/", response_model=TraceListResponse)
async def list_traces(
    session_id: str | None = Query(None),
    turn_id: str | None = Query(None),
    project_id: str | None = Query(None),
    environment: str | None = Query(None),
    service_instance_id: str | None = Query(None),
    status: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List traces with optional filtering and server-side search."""
    base_query = select(Trace)

    if session_id:
        base_query = base_query.where(Trace.session_id == session_id)

    if turn_id:
        base_query = base_query.where(Trace.turn_id == turn_id)

    if project_id:
        base_query = base_query.where(Trace.project_id == project_id)

    if environment:
        base_query = base_query.where(Trace.environment == environment)

    if service_instance_id:
        base_query = base_query.where(Trace.service_instance_id == service_instance_id)

    if status:
        base_query = base_query.where(Trace.status == status)

    if search:
        base_query = base_query.where(Trace.name.ilike(f"%{search}%"))

    # Get total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # Get paginated results
    data_query = base_query.order_by(Trace.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(data_query)
    traces = list(result.scalars().all())

    return {
        "traces": traces,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Export ──────────────────────────────────────────────────────────────


class ExportSpanData(BaseModel):
    """Span data in export format."""

    name: str
    span_type: str
    start_time: datetime
    end_time: datetime | None
    status: str
    input: dict | None = None
    output: dict | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    metadata: dict | None = None
    attributes: dict | None = None


class ExportTraceData(BaseModel):
    """Trace data in export format."""

    name: str
    session_id: str | None
    turn_id: str | None
    project_id: str | None
    environment: str | None
    service_instance_id: str | None
    actor_id_hash: str | None
    schema_version: str
    start_time: datetime
    end_time: datetime | None
    status: str
    metadata: dict | None
    spans: list[ExportSpanData]


class ExportResponse(BaseModel):
    """Response for trace export."""

    traces: list[ExportTraceData]
    total: int


@router.get("/export", response_model=ExportResponse)
async def export_traces(
    status: str | None = Query(None),
    session_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Export traces with spans in JSON format.

    Compatible with the import endpoint for migration between instances.
    """
    base_query = select(Trace)

    if status:
        base_query = base_query.where(Trace.status == status)

    if session_id:
        base_query = base_query.where(Trace.session_id == session_id)

    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    data_query = (
        base_query.options(selectinload(Trace.spans))
        .order_by(Trace.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(data_query)
    traces = list(result.scalars().unique().all())

    export_traces = []
    for trace in traces:
        export_spans = [
            ExportSpanData(
                name=s.name,
                span_type=s.span_type,
                start_time=s.start_time,
                end_time=s.end_time,
                status=s.status,
                input=s.inp,
                output=s.out,
                tokens_input=s.tokens_input,
                tokens_output=s.tokens_output,
                cost_usd=float(s.cost_usd) if s.cost_usd else None,
                metadata=s.meta,
                attributes=s.attributes,
            )
            for s in trace.spans
        ]
        export_traces.append(
            ExportTraceData(
                name=trace.name,
                session_id=trace.session_id,
                turn_id=trace.turn_id,
                project_id=trace.project_id,
                environment=trace.environment,
                service_instance_id=trace.service_instance_id,
                actor_id_hash=trace.actor_id_hash,
                schema_version=trace.schema_version,
                start_time=trace.start_time,
                end_time=trace.end_time,
                status=trace.status,
                metadata=trace.meta,
                spans=export_spans,
            )
        )

    return {"traces": export_traces, "total": total}


@router.get("/{trace_id}", response_model=TraceResponse)
async def get_trace(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
) -> Trace:
    """Get a trace by ID."""
    valid_id = await _resolve_trace_id(session, trace_id)

    query = select(Trace).where(Trace.id == valid_id)
    result = await session.execute(query)
    trace = result.scalar_one_or_none()

    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    return trace


@router.get("/{trace_id}/spans", response_model=list[SpanResponse])
async def get_trace_spans(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[Span]:
    """Get all spans for a trace."""
    valid_id = await _resolve_trace_id(session, trace_id)

    query = (
        select(Span)
        .where(Span.trace_id == valid_id)
        .order_by(Span.start_time)
    )
    result = await session.execute(query)
    return list(result.scalars().all())


@router.get("/{trace_id}/diagnostics")
async def get_trace_diagnostics(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Agent diagnostics of one trace: repeated tool calls (``app.loop_detection``)."""
    valid_id = await _resolve_trace_id(session, trace_id)
    if await session.get(Trace, valid_id) is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    rows = (await session.execute(select(Span).where(Span.trace_id == valid_id))).scalars().all()
    return {
        "trace_id": valid_id,
        "min_repeats": min_repeats(),
        "repeated_calls": repeated_calls(list(rows)),
    }


@router.delete("/{trace_id}")
async def delete_trace(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Delete a trace and its spans."""
    valid_id = await _resolve_trace_id(session, trace_id)

    query = select(Trace).where(Trace.id == valid_id)
    result = await session.execute(query)
    trace = result.scalar_one_or_none()

    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # SQLite ran without foreign keys (audit 2026-10-17)
    await session.execute(delete(Evaluation).where(Evaluation.trace_id == valid_id))
    await session.delete(trace)
    await session.flush()
    return {"detail": "Trace deleted"}


# ── Trace Evaluations ────────────────────────────────────────────────


@router.get("/{trace_id}/evaluations")
async def get_trace_evaluations(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Get all evaluations for a specific trace."""
    from app.models.evaluation import Evaluation

    valid_id = await _resolve_trace_id(session, trace_id)
    if await session.get(Trace, valid_id) is None:
        raise HTTPException(status_code=404, detail="Trace not found")

    query = (
        select(Evaluation)
        .where(Evaluation.trace_id == valid_id)
        .order_by(Evaluation.created_at.desc())
    )
    result = await session.execute(query)
    evaluations = list(result.scalars().all())

    return [
        {
            "id": e.id,
            "trace_id": e.trace_id,
            "span_id": e.span_id,
            "evaluator_type": e.evaluator_type,
            "score": float(e.score) if e.score is not None else None,
            "criteria": e.criteria,
            "result": e.result,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in evaluations
    ]


# ── Bulk Delete ──────────────────────────────────────────────────────


class BulkDeleteRequest(BaseModel):
    """Schema for bulk delete."""

    trace_ids: list[str]


@router.post("/batch-delete")
async def bulk_delete_traces(
    req: BulkDeleteRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete multiple traces by ID."""
    valid_ids = [tid for tid in (_safe_uuid(tid) for tid in req.trace_ids) if tid]
    if not valid_ids:
        raise HTTPException(status_code=400, detail="No valid trace IDs provided")

    query = select(Trace).where(Trace.id.in_(valid_ids))
    result = await session.execute(query)
    traces = list(result.scalars().all())

    deleted = 0
    if traces:
        trace_ids = [t.id for t in traces]
        await session.execute(delete(Evaluation).where(Evaluation.trace_id.in_(trace_ids)))
    for trace in traces:
        await session.delete(trace)
        deleted += 1

    await session.flush()
    return {"deleted": deleted, "requested": len(valid_ids)}


# ── Import ────────────────────────────────────────────────────────────


class ImportSpanData(BaseModel):
    """Span data for import."""

    name: str = Field(min_length=1, max_length=255)
    span_type: str = Field("generic", min_length=1, max_length=50)
    start_time: Timestamp
    end_time: OptionalTimestamp = None
    status: SpanStatusValue = "ok"
    input: dict | None = None
    output: dict | None = None
    tokens_input: int | None = Field(None, ge=0)
    tokens_output: int | None = Field(None, ge=0)
    cost_usd: float | None = Field(None, ge=0, allow_inf_nan=False)
    metadata: dict | None = None
    attributes: dict | None = None

    @field_validator("input", "output", "metadata", "attributes")
    @classmethod
    def _bounded(cls, value: dict | None) -> dict | None:
        return bounded_json(value)

    @model_validator(mode="after")
    def _interval(self) -> ImportSpanData:
        _check_interval(self.start_time, self.end_time)
        return self


class ImportTraceData(BaseModel):
    """Trace data for import."""

    name: str = Field(max_length=255)
    session_id: str | None = None
    turn_id: str | None = None
    project_id: str | None = None
    environment: str | None = None
    service_instance_id: str | None = None
    actor_id_hash: str | None = None
    schema_version: str = "observer.trace.v1"
    start_time: OptionalTimestamp = None
    end_time: OptionalTimestamp = None
    status: SpanStatusValue = "ok"
    metadata: dict | None = None
    spans: list[ImportSpanData] = Field(default_factory=list, max_length=MAX_SPANS_PER_TRACE)

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, value: dict | None) -> dict | None:
        return bounded_json(value)


class ImportRequest(BaseModel):
    """Request body for trace import."""

    traces: list[ImportTraceData] = Field(max_length=MAX_TRACES_PER_IMPORT)


class ImportResponse(BaseModel):
    """Response for trace import."""

    imported: int
    trace_ids: list[str]


@router.post("/import", response_model=ImportResponse)
@limiter.limit("10/minute")
async def import_traces(
    request: Request,
    import_data: ImportRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Import traces from a JSON export.

    Used for migrating data between observatory instances or restoring from backup.
    """
    trace_ids = []

    for trace_data in import_data.traces:
        start_dt = to_datetime(trace_data.start_time) or datetime.now(timezone.utc)
        end_dt = to_datetime(trace_data.end_time)

        trace = Trace(
            id=str(uuid.uuid4()),
            name=trace_data.name,
            session_id=trace_data.session_id,
            turn_id=trace_data.turn_id,
            project_id=trace_data.project_id,
            environment=trace_data.environment,
            service_instance_id=trace_data.service_instance_id,
            actor_id_hash=trace_data.actor_id_hash,
            schema_version=trace_data.schema_version,
            start_time=start_dt,
            end_time=end_dt,
            status=trace_data.status,
            meta=trace_data.metadata,
        )
        session.add(trace)
        await session.flush()

        mode = await capture_mode(session, trace.project_id, "full")
        for span_data in trace_data.spans:
            span_start = to_datetime(span_data.start_time)
            span_end = to_datetime(span_data.end_time)
            captured = apply_capture(
                mode,
                inp=span_data.input,
                out=span_data.output,
                attributes=span_data.attributes,
                meta=span_data.metadata,
            )
            span = Span(
                id=str(uuid.uuid4()),
                trace_id=trace.id,
                name=span_data.name,
                span_type=span_data.span_type,
                start_time=span_start,
                end_time=span_end,
                status=span_data.status,
                inp=captured[0],
                out=captured[1],
                tokens_input=span_data.tokens_input,
                tokens_output=span_data.tokens_output,
                cost_usd=span_data.cost_usd,
                meta=captured[3],
                attributes=captured[2],
            )
            session.add(span)

        trace_ids.append(str(trace.id))

    return {"imported": len(import_data.traces), "trace_ids": trace_ids}
