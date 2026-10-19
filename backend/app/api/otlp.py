"""OTLP/HTTP trace receiver (``POST /v1/otlp/v1/traces``).

Point a stock OpenTelemetry exporter at it:
``OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:8100/v1/otlp`` (the exporter appends ``/v1/traces``),
plus ``OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <key>`` when a key is required.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.manitos_ingest import ManitOSIngestRequest, apply_envelope
from app.db import DbSession
from app.models.trace import Trace
from app.otlp import (
    MAX_BODY_BYTES,
    OtlpError,
    decompress,
    request_from_json,
    request_from_protobuf,
    response_body,
    translate,
)
from app.otlp import (
    capture_mode as otlp_capture_mode,
)
from app.ratelimit import limiter
from app.trace_summary import refresh_trace_summary

router = APIRouter()

_OTLP_LIMIT = os.getenv("RATE_LIMIT_OTLP", "600/minute")


def _plain(status: int, message: str) -> Response:
    return Response(content=message, status_code=status, media_type="text/plain")


@router.post("/v1/traces")
@limiter.limit(_OTLP_LIMIT)
async def receive_traces(
    request: Request, session: AsyncSession = DbSession
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
        translated = translate(
            data, project_id=request.headers.get("x-observer-project"), mode="full"
        )
    except OtlpError as exc:
        return _plain(exc.status, str(exc))

    rejected = translated.rejected
    errors = list(translated.errors)
    for envelope in translated.envelopes:
        count = len(envelope["spans"])
        try:
            payload = ManitOSIngestRequest.model_validate(envelope)
            async with session.begin_nested():  # a rejected trace leaves no receipt behind
                await apply_envelope(
                    session,
                    payload,
                    default_capture=otlp_capture_mode(),
                    allow_pending_parents=True,
                )
                trace = await session.get(Trace, payload.trace.id)
                if trace is not None:
                    # status, interval and name over every batch of this trace, not this one
                    await refresh_trace_summary(session, trace)
        except ValidationError as exc:
            rejected += count
            errors.append(f"trace rejected: {exc.errors()[0]['msg']}"[:200])
        except Exception as exc:  # HTTPException from the shared ingestion: conflicts
            rejected += count
            errors.append(f"trace rejected: {getattr(exc, 'detail', exc.__class__.__name__)}"[:200])
    content, media = response_body(rejected, "; ".join(errors[:3]), protobuf=protobuf)
    return Response(content=content, media_type=media)
