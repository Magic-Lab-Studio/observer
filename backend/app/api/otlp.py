"""OTLP/HTTP trace receiver (``POST /v1/otlp/v1/traces``).

Point a stock OpenTelemetry exporter at it:
``OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:8100/v1/otlp`` (the exporter appends ``/v1/traces``),
plus ``OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <key>`` when a key is required.
"""

from __future__ import annotations

import os
from datetime import timezone
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.manitos_ingest import ManitOSIngestRequest, apply_envelope
from app.db import get_session
from app.models.trace import Span, Trace
from app.otlp import (
    MAX_BODY_BYTES,
    OtlpError,
    decompress,
    request_from_json,
    request_from_protobuf,
    response_body,
    translate,
)
from app.ratelimit import limiter

router = APIRouter()

_OTLP_LIMIT = os.getenv("RATE_LIMIT_OTLP", "600/minute")


def _plain(status: int, message: str) -> Response:
    return Response(content=message, status_code=status, media_type="text/plain")


async def _link_and_widen(session: AsyncSession, envelope: dict[str, Any]) -> None:
    """Spans of one trace can arrive in several batches: keep the trace's full time range and link
    a span to a parent stored by an earlier batch (instead of failing or orphaning it)."""
    trace_id = envelope["trace"]["id"]
    external = {s["_external_parent"] for s in envelope["spans"] if s.get("_external_parent")}
    stored: set[str] = set()
    if external:
        rows = await session.execute(
            select(Span.id).where(Span.id.in_(external), Span.trace_id == trace_id)
        )
        stored = {str(i) for i in rows.scalars()}
    for span in envelope["spans"]:
        parent = span.pop("_external_parent", None)
        if parent in stored:
            span["parent_id"] = parent
    existing = await session.get(Trace, trace_id)
    if existing is not None and existing.start_time is not None:
        trace = envelope["trace"]
        start = existing.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        trace["start_time"] = min(trace["start_time"], start.timestamp())
        ends = [trace["end_time"]] if trace.get("end_time") else []
        if existing.end_time is not None:
            end = existing.end_time
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            ends.append(end.timestamp())
        trace["end_time"] = max(ends) if ends else None
        trace["name"] = existing.name if existing.name else trace["name"]


@router.post("/v1/traces")
@limiter.limit(_OTLP_LIMIT)
async def receive_traces(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return _plain(413, "request too large")
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    protobuf = content_type == "application/x-protobuf"
    if not protobuf and content_type != "application/json":
        return _plain(415, "use application/x-protobuf or application/json")
    try:
        body = decompress(await request.body(), request.headers.get("content-encoding"))
        data = request_from_protobuf(body) if protobuf else request_from_json(body)
        translated = translate(data, project_id=request.headers.get("x-observer-project"))
    except OtlpError as exc:
        return _plain(exc.status, str(exc))

    rejected = translated.rejected
    errors = list(translated.errors)
    for envelope in translated.envelopes:
        count = len(envelope["spans"])
        try:
            await _link_and_widen(session, envelope)
            payload = ManitOSIngestRequest.model_validate(envelope)
            async with session.begin_nested():  # a rejected trace leaves no receipt behind
                await apply_envelope(session, payload)
        except ValidationError as exc:
            rejected += count
            errors.append(f"trace rejected: {exc.errors()[0]['msg']}"[:200])
        except Exception as exc:  # HTTPException from the shared ingestion: conflicts
            rejected += count
            errors.append(f"trace rejected: {getattr(exc, 'detail', exc.__class__.__name__)}"[:200])
    content, media = response_body(rejected, "; ".join(errors[:3]), protobuf=protobuf)
    return Response(content=content, media_type=media)
