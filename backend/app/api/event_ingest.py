"""``POST /v1/ingest/events``: a system's own JSON events, turned into traces by a mapping.

The body carries the mapping (YAML text or an object, see ``app.event_mapping``) and up to
``MAX_EVENTS`` events of one project. Events are grouped by trace, converted to
``manitos.telemetry.v1`` envelopes and stored through the same path as OTLP: an idempotency
receipt per envelope (ids and keys are deterministic, so sending the same events twice stores
nothing new) and the project's capture mode (default ``OBSERVATORY_EVENTS_CAPTURE``, else
``metadata``). ``dry_run`` returns what would be stored without touching the database.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.manitos_ingest import ManitOSIngestRequest, apply_envelope
from app.capture import MODES
from app.db import DbSession
from app.event_mapping import MappingError, events_to_envelopes, load_mapping
from app.models.trace import Trace
from app.ratelimit import limiter
from app.trace_summary import refresh_trace_summary

router = APIRouter()

MAX_EVENTS = 20_000
_LIMIT = os.getenv("RATE_LIMIT_EVENTS", "120/minute")


def capture_default() -> str:
    value = os.getenv("OBSERVATORY_EVENTS_CAPTURE", "metadata").strip().lower()
    return value if value in MODES else "metadata"


class EventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(default="production", min_length=1, max_length=64)
    mapping: str | dict[str, Any]
    events: list[Any] = Field(max_length=MAX_EVENTS)
    dry_run: bool = False


@router.post("")
@limiter.limit(_LIMIT)
async def ingest_events(
    request: Request, body: EventsRequest, session: AsyncSession = DbSession
) -> dict[str, Any]:
    try:
        mapping = load_mapping(body.mapping)
    except MappingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    envelopes, report = events_to_envelopes(
        mapping, body.events, project_id=body.project_id.strip(), environment=body.environment
    )
    result: dict[str, Any] = {"report": report.as_dict(), "dry_run": body.dry_run,
                              "accepted": 0, "duplicates": 0, "rejected": 0, "errors": []}
    if body.dry_run:
        result["traces"] = [
            {"trace_id": e["trace"]["id"], "name": e["trace"]["name"],
             "turn_id": e["turn_id"], "spans": len(e["spans"]),
             "start_time": e["trace"]["start_time"], "end_time": e["trace"]["end_time"]}
            for e in envelopes[:200]
        ]
        return result
    for envelope in envelopes:
        try:
            payload = ManitOSIngestRequest.model_validate(envelope)
            async with session.begin_nested():  # a rejected trace leaves no receipt behind
                response = await apply_envelope(
                    session, payload, default_capture=capture_default(),
                    allow_pending_parents=True,
                )
                trace = await session.get(Trace, payload.trace.id)
                if trace is not None:
                    await refresh_trace_summary(session, trace)
            key = "duplicates" if response.status == "duplicate" else "accepted"
            result[key] += 1
        except ValidationError as exc:
            result["rejected"] += 1
            result["errors"].append(f"trace {envelope['turn_id']}: {exc.errors()[0]['msg']}"[:200])
        except Exception as exc:  # HTTPException from the shared ingestion: conflicts
            result["rejected"] += 1
            detail = getattr(exc, "detail", exc.__class__.__name__)
            result["errors"].append(f"trace {envelope['turn_id']}: {detail}"[:200])
    result["errors"] = result["errors"][:20]
    return result
