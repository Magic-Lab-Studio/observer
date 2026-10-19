"""Field rules shared by the generic ingestion endpoints (``/v1/traces``, ``/batch``, ``/import``).

* **Timestamps** are Unix epoch seconds (int or float) or ISO 8601 strings with a time zone
  (``2026-10-18T12:00:00Z``); both are stored as UTC. A value must be finite, after 1970 and no
  later than 2100-01-01: milliseconds sent as seconds fall outside that range and are rejected
  with 422 instead of failing later with HTTP 500.
* **Identifiers** of traces, spans and parents are opaque strings of 1-128 characters without
  control characters. A UUID (any form ``uuid.UUID`` parses) is stored in canonical form; any
  other string is mapped deterministically to a UUIDv5 (span IDs within their trace), the way the
  OTLP receiver maps OpenTelemetry IDs. The same mapping applies to the detail routes, so
  ``GET /v1/traces/demo-001`` finds the trace ingested as ``demo-001`` and a parent given as
  ``span-a`` links to the span sent as ``span-a``. Before this a non-UUID trace was stored but
  could never be opened, and a non-UUID parent was silently dropped.
* **Status** is one of ``unset``, ``ok``, ``error``.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator

MAX_TIMESTAMP = 4_102_444_800.0  # 2100-01-01T00:00:00Z

SpanStatusValue = Literal["unset", "ok", "error"]


def parse_timestamp(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("timestamp must be epoch seconds or an ISO 8601 string")
    if isinstance(value, str):
        text = value.strip()
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    "timestamp must be epoch seconds or an ISO 8601 string"
                ) from exc
            if parsed.tzinfo is None:
                raise ValueError("ISO 8601 timestamp needs a time zone (for example Z)")
            number = parsed.astimezone(timezone.utc).timestamp()
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise ValueError("timestamp must be epoch seconds or an ISO 8601 string")
    if not math.isfinite(number) or number <= 0 or number > MAX_TIMESTAMP:
        raise ValueError(
            "timestamp must be epoch seconds after 1970 and no later than 2100-01-01"
        )
    return number


_ID_NAMESPACE = uuid.UUID("5f0c3a8e-2d7b-4f1e-9a63-0b8e4c2d1f70")
MAX_ID_LENGTH = 128


def opaque_id(value: Any) -> str | None:
    """A client identifier as given: a string, stripped, without control characters."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("identifier must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_ID_LENGTH or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(
            f"identifier must be 1-{MAX_ID_LENGTH} characters without control characters"
        )
    return text


def _as_uuid(text: str) -> str | None:
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def trace_storage_id(value: str) -> str:
    """The stored ID of a trace given by the client (see the module docstring)."""
    return _as_uuid(value) or str(uuid.uuid5(_ID_NAMESPACE, f"trace:{value}"))


def span_storage_id(value: str, trace_id: str | None) -> str:
    """The stored ID of a span or parent given by the client, scoped to its (stored) trace."""
    return _as_uuid(value) or str(uuid.uuid5(_ID_NAMESPACE, f"span:{trace_id or ''}:{value}"))


def canonical_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a UUID string")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError as exc:
        raise ValueError("must be a UUID (for example 3f2b8c4e-...)") from exc


def optional_uuid(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return canonical_uuid(value)


def canonical_or_none(value: str | None) -> str | None:
    """For path parameters: the canonical UUID, or None when it is not one."""
    try:
        return optional_uuid(value)
    except ValueError:
        return None


def trace_lookup_id(value: str | None) -> str | None:
    """For path parameters: the stored ID a client trace ID maps to, or None when invalid."""
    try:
        text = opaque_id(value)
    except ValueError:
        return None
    return trace_storage_id(text) if text else None


Timestamp = Annotated[float, BeforeValidator(parse_timestamp)]
OptionalTimestamp = Annotated[
    float | None, BeforeValidator(lambda v: None if v is None else parse_timestamp(v))
]
UUIDString = Annotated[str, BeforeValidator(canonical_uuid)]
OptionalUUIDString = Annotated[str | None, BeforeValidator(optional_uuid)]
OpaqueId = Annotated[str | None, BeforeValidator(opaque_id)]


def to_datetime(timestamp: float | None) -> datetime | None:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp is not None else None
