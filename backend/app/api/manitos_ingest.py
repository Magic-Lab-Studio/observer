"""Versioned, idempotent telemetry ingestion for a reference runtime integration."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.ingestion import IngestionReceipt
from app.models.trace import Span, Trace
from app.ratelimit import limiter
from app.websocket import manager

router = APIRouter()

_SCHEMA_VERSION = "manitos.telemetry.v1"
_MAX_ENVELOPE_BYTES = 2 * 1024 * 1024
_MAX_JSON_FIELD_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 8
_MANITOS_BATCH_LIMIT = os.getenv("RATE_LIMIT_MANITOS_BATCH", "120/minute")


class StrictModel(BaseModel):
    """Reject unknown fields so producer and server cannot silently diverge."""

    model_config = ConfigDict(extra="forbid")


def _uuid_string(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("must be a valid UUID") from exc


def _json_depth(value: Any, depth: int = 0) -> int:
    if depth > _MAX_JSON_DEPTH:
        return depth
    if isinstance(value, dict):
        return max((_json_depth(item, depth + 1) for item in value.values()), default=depth)
    if isinstance(value, list):
        return max((_json_depth(item, depth + 1) for item in value), default=depth)
    return depth


def _bounded_json(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > _MAX_JSON_FIELD_BYTES:
        raise ValueError(f"JSON field exceeds {_MAX_JSON_FIELD_BYTES} bytes")
    if _json_depth(value) > _MAX_JSON_DEPTH:
        raise ValueError(f"JSON field exceeds maximum depth {_MAX_JSON_DEPTH}")
    return value


class ManitOSTrace(StrictModel):
    """Root trace metadata for one integration operation."""

    id: str
    name: str = Field(min_length=1, max_length=255)
    start_time: float = Field(gt=0)
    end_time: float | None = Field(default=None, gt=0)
    status: Literal["unset", "ok", "error"] = "unset"
    metadata: dict[str, Any] | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _uuid_string(value)

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_timestamp(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value > 4_102_444_800):
            raise ValueError("timestamp must be finite and no later than 2100-01-01")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value)

    @model_validator(mode="after")
    def validate_interval(self) -> ManitOSTrace:
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time cannot precede start_time")
        return self


class ManitOSSpan(StrictModel):
    """A completed or explicitly partial operation within an integration trace."""

    id: str
    parent_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    span_type: str = Field(default="generic", min_length=1, max_length=50)
    start_time: float = Field(gt=0)
    end_time: float | None = Field(default=None, gt=0)
    status: Literal["unset", "ok", "error"] = "unset"
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    tokens_input: int | None = Field(default=None, ge=0)
    tokens_output: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] | None = None
    attributes: dict[str, Any] | None = None

    @field_validator("id", "parent_id")
    @classmethod
    def validate_ids(cls, value: str | None) -> str | None:
        return _uuid_string(value) if value is not None else None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_timestamp(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value > 4_102_444_800):
            raise ValueError("timestamp must be finite and no later than 2100-01-01")
        return value

    @field_validator("input", "output", "metadata", "attributes")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return _bounded_json(value)

    @model_validator(mode="after")
    def validate_interval(self) -> ManitOSSpan:
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time cannot precede start_time")
        return self


class ManitOSIngestRequest(StrictModel):
    """Stable wire envelope retained for the published integration contract."""

    schema_version: Literal["manitos.telemetry.v1"]
    idempotency_key: str = Field(min_length=1, max_length=255)
    project_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=64)
    service_instance_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=255)
    turn_id: str = Field(min_length=1, max_length=128)
    actor_id_hash: str | None = Field(default=None, max_length=128)
    trace: ManitOSTrace
    spans: list[ManitOSSpan] = Field(min_length=1, max_length=500)

    @field_validator(
        "idempotency_key",
        "project_id",
        "environment",
        "service_instance_id",
        "session_id",
        "turn_id",
        "actor_id_hash",
    )
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("must be non-empty and contain no control characters")
        return normalized

    @model_validator(mode="after")
    def validate_envelope(self) -> ManitOSIngestRequest:
        span_ids = [span.id for span in self.spans]
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("span IDs must be unique within a request")
        parents = {span.id: span.parent_id for span in self.spans if span.parent_id in span_ids}
        for span_id in span_ids:
            visited: set[str] = set()
            current: str | None = span_id
            while current in parents:
                if current in visited:
                    raise ValueError("span parent relationships must be acyclic")
                visited.add(current)
                current = parents[current]
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        if len(encoded) > _MAX_ENVELOPE_BYTES:
            raise ValueError(f"request exceeds {_MAX_ENVELOPE_BYTES} bytes")
        return self


class ManitOSIngestResponse(StrictModel):
    """Deterministic result suitable for exporter retry accounting."""

    schema_version: Literal["manitos.telemetry.v1"] = _SCHEMA_VERSION
    idempotency_key: str
    trace_id: str
    status: Literal["accepted", "duplicate"]
    accepted_spans: int
    updated_spans: int
    duplicate_spans: int
    rejected_spans: int = 0


def _payload_hash(payload: ManitOSIngestRequest) -> str:
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _as_datetime(timestamp: float | None) -> datetime | None:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp is not None else None


def _datetime_equal(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return abs(left.timestamp() - right.timestamp()) < 0.000001


def _span_values(span: ManitOSSpan) -> dict[str, Any]:
    return {
        "name": span.name,
        "span_type": span.span_type,
        "start_time": _as_datetime(span.start_time),
        "end_time": _as_datetime(span.end_time),
        "status": span.status,
        "inp": span.input,
        "out": span.output,
        "tokens_input": span.tokens_input,
        "tokens_output": span.tokens_output,
        "cost_usd": span.cost_usd,
        "meta": span.metadata,
        "attributes": span.attributes,
    }


def _span_matches(existing: Span, values: dict[str, Any]) -> bool:
    for key, value in values.items():
        current = getattr(existing, key)
        if key in {"start_time", "end_time"}:
            if not _datetime_equal(current, value):
                return False
        elif key == "cost_usd":
            if (float(current) if current is not None else None) != value:
                return False
        elif current != value:
            return False
    return True


def _apply_values(target: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(target, key, value)


async def _existing_receipt(
    session: AsyncSession,
    *,
    project_id: str,
    idempotency_key: str,
) -> IngestionReceipt | None:
    result = await session.execute(
        select(IngestionReceipt).where(
            IngestionReceipt.project_id == project_id,
            IngestionReceipt.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


def _duplicate_response(
    payload: ManitOSIngestRequest,
    receipt: IngestionReceipt,
) -> ManitOSIngestResponse:
    stored = dict(receipt.response or {})
    return ManitOSIngestResponse(
        idempotency_key=payload.idempotency_key,
        trace_id=str(receipt.trace_id),
        status="duplicate",
        accepted_spans=0,
        updated_spans=0,
        duplicate_spans=len(payload.spans),
        rejected_spans=int(stored.get("rejected_spans") or 0),
    )


@router.post("/traces", response_model=ManitOSIngestResponse)
@limiter.limit(_MANITOS_BATCH_LIMIT)
async def ingest_manitos_trace(
    request: Request,
    payload: ManitOSIngestRequest,
    session: AsyncSession = Depends(get_session),
) -> ManitOSIngestResponse:
    """Upsert a complete integration trace with retry-safe semantics."""

    del request  # Required by SlowAPI's decorator contract.
    digest = _payload_hash(payload)
    receipt = await _existing_receipt(
        session,
        project_id=payload.project_id,
        idempotency_key=payload.idempotency_key,
    )
    if receipt is not None:
        if receipt.payload_sha256 != digest:
            raise HTTPException(
                status_code=409,
                detail="idempotency key was already used with a different payload",
            )
        return _duplicate_response(payload, receipt)

    receipt = IngestionReceipt(
        project_id=payload.project_id,
        idempotency_key=payload.idempotency_key,
        payload_sha256=digest,
        trace_id=payload.trace.id,
    )
    session.add(receipt)
    try:
        await session.flush()
    except IntegrityError:
        # A concurrent retry won the unique-key race.  Re-open the transaction
        # and return the same deterministic result instead of leaking a 500.
        await session.rollback()
        concurrent = await _existing_receipt(
            session,
            project_id=payload.project_id,
            idempotency_key=payload.idempotency_key,
        )
        if concurrent is None or concurrent.payload_sha256 != digest:
            raise HTTPException(status_code=409, detail="conflicting concurrent ingestion")
        return _duplicate_response(payload, concurrent)

    trace = await session.get(Trace, payload.trace.id)
    trace_values = {
        "name": payload.trace.name,
        "session_id": payload.session_id,
        "turn_id": payload.turn_id,
        "project_id": payload.project_id,
        "environment": payload.environment,
        "service_instance_id": payload.service_instance_id,
        "actor_id_hash": payload.actor_id_hash,
        "schema_version": payload.schema_version,
        "start_time": _as_datetime(payload.trace.start_time),
        "end_time": _as_datetime(payload.trace.end_time),
        "status": payload.trace.status,
        "meta": payload.trace.metadata,
    }
    if trace is None:
        trace = Trace(id=payload.trace.id, **trace_values)
        session.add(trace)
    elif trace.project_id not in {None, payload.project_id}:
        raise HTTPException(status_code=409, detail="trace ID belongs to another project")
    else:
        _apply_values(trace, trace_values)

    span_ids = [span.id for span in payload.spans]
    result = await session.execute(select(Span).where(Span.id.in_(span_ids)))
    existing_by_id = {str(span.id): span for span in result.scalars().all()}
    for existing in existing_by_id.values():
        if str(existing.trace_id) != payload.trace.id:
            raise HTTPException(
                status_code=409,
                detail=f"span {existing.id} belongs to another trace",
            )

    external_parent_ids = {
        span.parent_id
        for span in payload.spans
        if span.parent_id and span.parent_id not in span_ids
    }
    if external_parent_ids:
        parent_result = await session.execute(select(Span).where(Span.id.in_(external_parent_ids)))
        valid_parents = {
            str(span.id)
            for span in parent_result.scalars().all()
            if str(span.trace_id) == payload.trace.id
        }
        missing_parents = external_parent_ids - valid_parents
        if missing_parents:
            raise HTTPException(status_code=409, detail="parent span does not belong to this trace")

    accepted = 0
    updated = 0
    unchanged = 0
    materialized: dict[str, Span] = {}
    values_by_id: dict[str, dict[str, Any]] = {}
    for incoming in payload.spans:
        values = _span_values(incoming)
        values_by_id[incoming.id] = values
        existing = existing_by_id.get(incoming.id)
        if existing is None:
            existing = Span(
                id=incoming.id,
                trace_id=payload.trace.id,
                parent_span_id=None,
                **values,
            )
            session.add(existing)
            accepted += 1
        elif _span_matches(existing, values) and existing.parent_span_id == incoming.parent_id:
            unchanged += 1
        else:
            _apply_values(existing, values)
            updated += 1
        materialized[incoming.id] = existing

    # Insert all children before assigning their self-referential parents so
    # PostgreSQL and SQLite observe the same foreign-key ordering.
    await session.flush()
    for incoming in payload.spans:
        materialized[incoming.id].parent_span_id = incoming.parent_id

    response = ManitOSIngestResponse(
        idempotency_key=payload.idempotency_key,
        trace_id=payload.trace.id,
        status="accepted",
        accepted_spans=accepted,
        updated_spans=updated,
        duplicate_spans=unchanged,
    )
    receipt.response = response.model_dump(mode="json")
    await session.flush()

    await manager.broadcast(
        {
            "type": "new_trace",
            "data": {
                "id": payload.trace.id,
                "name": payload.trace.name,
                "status": payload.trace.status,
                "project_id": payload.project_id,
                "schema_version": payload.schema_version,
            },
        }
    )
    return response
