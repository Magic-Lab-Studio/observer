"""Render bed episodes in the formats Observer ingests.

* ``batch``: the Python SDK wire format, ``POST /v1/traces/batch`` (``{"spans": [...]}``);
* ``otlp``: OTLP/JSON, ``POST /v1/otlp/v1/traces``. OTLP has no span type, so contract spans
  carry ``observer.span.type``.

Ids are derived from the project, the run and the episode, so one run is reproducible and two
runs (or two projects) never collide.
"""

from __future__ import annotations

import uuid
from typing import Any

from .model import Episode

_NS = uuid.UUID("6f0f5a62-3c1b-4f39-9d6c-1b8c0b9e4a11")


def trace_id(project: str, run: str, episode: Episode) -> str:
    return str(uuid.uuid5(_NS, f"{project}:{run}:{episode.key}"))


def _span_id(project: str, run: str, episode: Episode, key: str) -> str:
    return str(uuid.uuid5(_NS, f"{project}:{run}:{episode.key}:{key}"))


def to_batch(episodes: list[Episode], *, project: str, run: str, base_s: float) -> dict[str, Any]:
    spans = []
    for ep in episodes:
        tid = trace_id(project, run, ep)
        t0 = base_s + ep.offset_s
        for s in ep.spans:
            spans.append({
                "id": _span_id(project, run, ep, s.key),
                "trace_id": tid,
                "parent_id": _span_id(project, run, ep, s.parent) if s.parent else None,
                "name": s.name,
                "span_type": s.span_type,
                "start_time": t0 + s.start_ms / 1000,
                "end_time": t0 + s.end_ms / 1000,
                "status": s.status,
                "attributes": {"service.name": project, **s.attrs},
            })
    return {"spans": spans}


def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, list):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    return {"stringValue": str(v)}


_OTLP_STATUS = {"ok": 1, "error": 2}


def to_otlp(episodes: list[Episode], *, project: str, run: str, base_s: float) -> dict[str, Any]:
    spans = []
    for ep in episodes:
        trace_hex = uuid.UUID(trace_id(project, run, ep)).hex
        t0_ns = int((base_s + ep.offset_s) * 1e9)

        def sid(key: str, ep: Episode = ep) -> str:
            return uuid.UUID(_span_id(project, run, ep, key)).hex[:16]

        for s in ep.spans:
            attrs = dict(s.attrs)
            if s.span_type in {"decision", "tool", "retrieval", "gate", "verification", "replan",
                               "close"}:
                attrs["observer.span.type"] = s.span_type
            spans.append({
                "traceId": trace_hex,
                "spanId": sid(s.key),
                "parentSpanId": sid(s.parent) if s.parent else "",
                "name": s.name,
                "startTimeUnixNano": str(t0_ns + int(s.start_ms * 1e6)),
                "endTimeUnixNano": str(t0_ns + int(s.end_ms * 1e6)),
                "status": {"code": _OTLP_STATUS.get(s.status, 0)},
                "attributes": [{"key": k, "value": _value(v)} for k, v in attrs.items()],
            })
    return {"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": project}}]},
        "scopeSpans": [{"scope": {"name": "orchestrator-bed"}, "spans": spans}],
    }]}
