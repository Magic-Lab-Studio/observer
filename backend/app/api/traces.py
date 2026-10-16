"""Trace API endpoints."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.capture import apply_capture, capture_mode
from app.db import get_session
from app.models.trace import Span, Trace
from app.ratelimit import limiter
from app.websocket import manager

router = APIRouter()

# The SDK flushes every few seconds, so the old fixed 10/minute rejected healthy senders.
_BATCH_LIMIT = os.getenv("RATE_LIMIT_BATCH", "120/minute")


def _safe_uuid(value: str | None) -> str | None:
    """Validate and return a UUID string, or None if invalid."""
    if not value:
        return None
    try:
        uuid.UUID(value)
        return value
    except ValueError:
        return None


class SpanCreate(BaseModel):
    """Schema for creating a span."""

    id: str | None = None
    trace_id: str | None = None
    parent_id: str | None = None
    name: str
    span_type: str = "generic"
    start_time: float
    end_time: float | None = None
    status: str = "unset"
    input: dict | None = None
    output: dict | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    metadata: dict | None = None
    attributes: dict | None = None


class TraceCreate(BaseModel):
    """Schema for creating a trace."""

    name: str
    session_id: str | None = None
    start_time: float
    end_time: float | None = None
    status: str = "unset"
    metadata: dict | None = None
    spans: list[SpanCreate] = []

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

    spans: list[SpanCreate] = []


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
        start_time=datetime.fromtimestamp(trace_data.start_time, tz=timezone.utc),
        end_time=(
            datetime.fromtimestamp(trace_data.end_time, tz=timezone.utc)
            if trace_data.end_time
            else None
        ),
        status=trace_data.status,
        meta=trace_data.metadata,
    )
    session.add(trace)

    # Add spans
    mode = await capture_mode(session, trace.project_id, "full")
    for span_data in trace_data.spans:
        span_end = (
            datetime.fromtimestamp(span_data.end_time, tz=timezone.utc)
            if span_data.end_time
            else None
        )
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
            parent_span_id=_safe_uuid(span_data.parent_id),
            name=span_data.name,
            span_type=span_data.span_type,
            start_time=datetime.fromtimestamp(span_data.start_time, tz=timezone.utc),
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


@router.post("/batch", response_model=list[TraceResponse])
@limiter.limit(_BATCH_LIMIT)
async def create_traces_batch(
    request: Request,
    batch_data: BatchSpansCreate,
    session: AsyncSession = Depends(get_session),
) -> list[Trace]:
    """Create traces from a batch of spans.

    Spans are grouped by trace_id. If a trace_id doesn't exist, a new trace is created.
    This endpoint is designed for SDK exporters that send spans directly.
    """
    # Group spans by trace_id
    spans_by_trace: dict[str, list[SpanCreate]] = {}
    for span_data in batch_data.spans:
        trace_id = span_data.trace_id or str(uuid.uuid4())
        if trace_id not in spans_by_trace:
            spans_by_trace[trace_id] = []
        spans_by_trace[trace_id].append(span_data)

    created_traces: list[Trace] = []

    # A retried batch (the first answer was lost) must not fail on spans already stored.
    wanted = [s.id for spans in spans_by_trace.values() for s in spans if s.id]
    stored_ids: set[str] = set()
    if wanted:
        rows = await session.execute(select(Span.id).where(Span.id.in_(wanted)))
        stored_ids = {str(i) for i in rows.scalars()}

    for trace_id, spans in spans_by_trace.items():
        # Check if trace already exists
        query = select(Trace).where(Trace.id == trace_id)
        result = await session.execute(query)
        trace = result.scalar_one_or_none()

        if not trace:
            # Create trace from first span's timing
            first_span = spans[0]
            trace = Trace(
                id=trace_id,
                name=first_span.name,
                start_time=datetime.fromtimestamp(first_span.start_time, tz=timezone.utc),
                end_time=(
                    datetime.fromtimestamp(spans[-1].end_time, tz=timezone.utc)
                    if spans[-1].end_time
                    else None
                ),
                status="ok" if all(s.status == "ok" for s in spans) else "error",
            )
            session.add(trace)

        # Add spans
        mode = await capture_mode(session, trace.project_id, "full")
        for span_data in spans:
            if span_data.id and span_data.id in stored_ids:
                continue
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
                parent_span_id=_safe_uuid(span_data.parent_id),
                name=span_data.name,
                span_type=span_data.span_type,
                start_time=datetime.fromtimestamp(span_data.start_time, tz=timezone.utc),
                end_time=(
                    datetime.fromtimestamp(span_data.end_time, tz=timezone.utc)
                    if span_data.end_time
                    else None
                ),
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

        created_traces.append(trace)

    await session.flush()

    # Broadcast new traces via WebSocket
    for trace in created_traces:
        await manager.broadcast({
            "type": "new_trace",
            "data": {
                "id": str(trace.id),
                "name": trace.name,
                "status": trace.status,
                "created_at": trace.created_at.isoformat() if trace.created_at else None,
            },
        })

    return created_traces


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
    valid_id = _safe_uuid(trace_id)
    if not valid_id:
        raise HTTPException(status_code=400, detail="Invalid trace ID format")

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
    valid_id = _safe_uuid(trace_id)
    if not valid_id:
        raise HTTPException(status_code=400, detail="Invalid trace ID format")

    query = (
        select(Span)
        .where(Span.trace_id == valid_id)
        .order_by(Span.start_time)
    )
    result = await session.execute(query)
    return list(result.scalars().all())


@router.delete("/{trace_id}")
async def delete_trace(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Delete a trace and its spans."""
    valid_id = _safe_uuid(trace_id)
    if not valid_id:
        raise HTTPException(status_code=400, detail="Invalid trace ID format")

    query = select(Trace).where(Trace.id == valid_id)
    result = await session.execute(query)
    trace = result.scalar_one_or_none()

    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

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

    valid_id = _safe_uuid(trace_id)
    if not valid_id:
        raise HTTPException(status_code=400, detail="Invalid trace ID format")

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
    for trace in traces:
        await session.delete(trace)
        deleted += 1

    await session.flush()
    return {"deleted": deleted, "requested": len(valid_ids)}


# ── Import ────────────────────────────────────────────────────────────


class ImportSpanData(BaseModel):
    """Span data for import."""

    name: str
    span_type: str = "generic"
    start_time: float
    end_time: float | None = None
    status: str = "ok"
    input: dict | None = None
    output: dict | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    metadata: dict | None = None
    attributes: dict | None = None


class ImportTraceData(BaseModel):
    """Trace data for import."""

    name: str
    session_id: str | None = None
    turn_id: str | None = None
    project_id: str | None = None
    environment: str | None = None
    service_instance_id: str | None = None
    actor_id_hash: str | None = None
    schema_version: str = "observer.trace.v1"
    start_time: float | None = None
    end_time: float | None = None
    status: str = "ok"
    metadata: dict | None = None
    spans: list[ImportSpanData] = []


class ImportRequest(BaseModel):
    """Request body for trace import."""

    traces: list[ImportTraceData]


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
        start_dt = (
            datetime.fromtimestamp(trace_data.start_time, tz=timezone.utc)
            if trace_data.start_time
            else datetime.now(timezone.utc)
        )
        end_dt = (
            datetime.fromtimestamp(trace_data.end_time, tz=timezone.utc)
            if trace_data.end_time
            else None
        )

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
            span_start = datetime.fromtimestamp(span_data.start_time, tz=timezone.utc)
            span_end = (
                datetime.fromtimestamp(span_data.end_time, tz=timezone.utc)
                if span_data.end_time
                else None
            )
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
