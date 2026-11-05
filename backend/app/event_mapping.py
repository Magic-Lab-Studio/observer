"""Turn a system's own JSON events into traces, with a declarative mapping (roadmap step 11).

Many agents already write structured events (``job_id``, an event name, a timestamp,
started/completed pairs) and call their models through their own layer, so neither the SDK
instrumentors nor OTLP see them. A mapping says, in YAML:

* which field names the event and which holds its time (``time.unit``: ``s``, ``ms`` or ``iso``);
* which field groups events into one trace (``trace.key``), and optionally its session;
* which event pairs form a span (``start`` / ``end`` event names; a span with only ``end`` uses
  ``duration_field`` or is instant), how instances are told apart (``key``) and linked (``parent``,
  a field holding the parent's key), its name and type (literal or from a field), its status;
* which fields become attributes (``attributes``: a list of names, or ``{attribute: field}``) and
  which are content (``content.input`` / ``content.output``, kept under the project's capture mode).

The mapping is closed: an unknown key is an error. A field the mapping does not name never reaches
the database. Ids are deterministic (a trace key that is a UUID is the trace id; anything else is
a UUIDv5 of the project and the key), so ingesting the same events twice creates nothing new.

The output is ``manitos.telemetry.v1`` envelopes, stored through the same path as OTLP.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_TRACE_NS = uuid.UUID("0b3f6a9e-7c41-4d2a-9e85-3f1c6d2b8a17")
_SPAN_NS = uuid.UUID("9d27c4e1-5a3b-4f86-b0d2-6e8a1c4f7b39")
SPANS_PER_ENVELOPE = 500
MAX_ATTR_TEXT = 1000
_STATUS = {"ok": "ok", "success": "ok", "succeeded": "ok", "completed": "ok", "error": "error",
           "failed": "error", "failure": "error", "unset": "unset"}


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeSpec(_Closed):
    field: str = Field(min_length=1)
    unit: Literal["s", "ms", "iso"] = "s"


class TraceSpec(_Closed):
    key: str = Field(min_length=1, description="field that groups events into one trace")
    name: str = Field(default="events.trace", min_length=1, max_length=255)
    session: str | None = None


class ContentSpec(_Closed):
    input: str | None = None
    output: str | None = None


class SpanSpec(_Closed):
    end: str = Field(min_length=1, description="event name that ends the span")
    start: str | None = None
    key: str | None = None
    parent: str | None = None
    name: str | None = Field(default=None, max_length=255)
    name_field: str | None = None
    type: str = Field(default="generic", min_length=1, max_length=50)
    type_field: str | None = None
    status_field: str | None = None
    duration_field: str | None = None
    attributes: list[str] | dict[str, str] = Field(default_factory=list)
    content: ContentSpec | None = None

    @model_validator(mode="after")
    def _named(self) -> SpanSpec:
        if not self.name and not self.name_field:
            self.name = self.end
        return self

    def attribute_fields(self) -> dict[str, str]:
        if isinstance(self.attributes, dict):
            return dict(self.attributes)
        return {name: name for name in self.attributes}


class EventMapping(_Closed):
    version: Literal[1] = 1
    event: str = Field(min_length=1, description="field holding the event name")
    time: TimeSpec
    trace: TraceSpec
    spans: list[SpanSpec] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _distinct_events(self) -> EventMapping:
        seen: dict[str, int] = {}
        for i, spec in enumerate(self.spans):
            for name in filter(None, (spec.start, spec.end)):
                if seen.setdefault(name, i) != i:
                    raise ValueError(f"event {name!r} is used by two span specs")
        return self


class MappingError(ValueError):
    """The mapping is not valid YAML or does not follow the schema."""


def load_mapping(source: str | dict[str, Any]) -> EventMapping:
    """A mapping from YAML text or an already parsed dict."""
    try:
        data = yaml.safe_load(source) if isinstance(source, str) else source
    except yaml.YAMLError as exc:
        raise MappingError(f"mapping is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise MappingError("mapping must be a YAML mapping")
    try:
        return EventMapping.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise MappingError(f"mapping {where}: {first.get('msg')}") from None


@dataclass
class Report:
    events: int = 0
    used: int = 0
    ignored: int = 0  # an event name no span spec uses
    no_trace_key: int = 0
    bad_time: int = 0
    not_an_object: int = 0
    unfinished: int = 0  # started, never ended: sent as a partial span
    traces: int = 0
    spans: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class _Instance:
    spec_index: int
    key: str
    start: float | None = None
    end: float | None = None
    fields: dict[str, Any] = field(default_factory=dict)


def _timestamp(value: Any, unit: str) -> float | None:
    try:
        if unit == "iso":
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            number = parsed.astimezone(timezone.utc).timestamp()
        else:
            if isinstance(value, bool):
                return None
            number = float(value) / (1000.0 if unit == "ms" else 1.0)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 < number <= 4_102_444_800 else None


def _attr_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_ATTR_TEXT]
    if isinstance(value, list) and all(v is None or isinstance(v, (bool, int, float, str))
                                       for v in value):
        return [v[:MAX_ATTR_TEXT] if isinstance(v, str) else v for v in value[:64]]
    return None  # objects are not attributes; map their fields one by one


def trace_uuid(project_id: str, key: str) -> str:
    try:
        return str(uuid.UUID(key))
    except ValueError:
        return str(uuid.uuid5(_TRACE_NS, f"{project_id}:{key}"))


def _span_uuid(trace_id: str, key: str) -> str:
    return str(uuid.uuid5(_SPAN_NS, f"{trace_id}:{key}"))


def events_to_envelopes(
    mapping: EventMapping,
    events: list[Any],
    *,
    project_id: str,
    environment: str = "production",
    service_instance_id: str = "observer-ingest",
) -> tuple[list[dict[str, Any]], Report]:
    """``manitos.telemetry.v1`` envelopes for the events, and what happened to each event."""
    report = Report(events=len(events))
    by_event: dict[str, tuple[int, str]] = {}
    for i, spec in enumerate(mapping.spans):
        if spec.start:
            by_event[spec.start] = (i, "start")
        by_event[spec.end] = (i, "end")

    traces: OrderedDict[str, OrderedDict[str, _Instance]] = OrderedDict()
    sessions: dict[str, str] = {}
    for event in events:
        if not isinstance(event, dict):
            report.not_an_object += 1
            continue
        hit = by_event.get(str(event.get(mapping.event) or ""))
        if hit is None:
            report.ignored += 1
            continue
        raw_key = event.get(mapping.trace.key)
        if raw_key is None or str(raw_key).strip() == "":
            report.no_trace_key += 1
            continue
        when = _timestamp(event.get(mapping.time.field), mapping.time.unit)
        if when is None:
            report.bad_time += 1
            continue
        report.used += 1
        trace_key = str(raw_key).strip()[:128]
        index, edge = hit
        spec = mapping.spans[index]
        instance_key = str(event.get(spec.key)) if spec.key and event.get(spec.key) is not None \
            else f"#{index}"
        instances = traces.setdefault(trace_key, OrderedDict())
        inst = instances.setdefault(f"{index}|{instance_key}", _Instance(index, instance_key))
        if edge == "start":
            inst.start = when if inst.start is None else min(inst.start, when)
            inst.fields = {**event, **inst.fields}  # the end event's fields win
        else:
            inst.end = when if inst.end is None else max(inst.end, when)
            inst.fields = {**inst.fields, **event}
        if mapping.trace.session and event.get(mapping.trace.session) is not None:
            sessions.setdefault(trace_key, str(event.get(mapping.trace.session))[:255])

    envelopes: list[dict[str, Any]] = []
    for trace_key, instances in traces.items():
        trace_id = trace_uuid(project_id, trace_key)
        ids = {}
        for inst in instances.values():
            ids.setdefault(inst.key, _span_uuid(trace_id, inst.key))
        spans = [_span(mapping.spans[inst.spec_index], inst, trace_id, ids, report)
                 for inst in instances.values()]
        report.traces += 1
        report.spans += len(spans)
        start = min(s["start_time"] for s in spans)
        ends = [s["end_time"] for s in spans if s["end_time"] is not None]
        status = "error" if any(s["status"] == "error" for s in spans) else "ok"
        roots = [s for s in spans if s["parent_id"] is None]
        name = mapping.trace.name if mapping.trace.name != "events.trace" or not roots \
            else roots[0]["name"]
        for chunk_start in range(0, len(spans), SPANS_PER_ENVELOPE):
            chunk = spans[chunk_start:chunk_start + SPANS_PER_ENVELOPE]
            envelope = {
                "schema_version": "manitos.telemetry.v1",
                "project_id": project_id,
                "environment": environment,
                "service_instance_id": service_instance_id,
                "session_id": sessions.get(trace_key, trace_key),
                "turn_id": trace_key,
                "trace": {"id": trace_id, "name": name, "start_time": start,
                          "end_time": max(ends) if ends else None, "status": status,
                          "metadata": {"source": "events"}},
                "spans": chunk,
            }
            digest = hashlib.sha256(json.dumps(envelope, sort_keys=True, default=str)
                                    .encode()).hexdigest()
            envelope["idempotency_key"] = f"events:{digest[:48]}"
            envelopes.append(envelope)
    return envelopes, report


def _span(spec: SpanSpec, inst: _Instance, trace_id: str, ids: dict[str, str],
          report: Report) -> dict[str, Any]:
    fields = inst.fields
    end = inst.end
    start = inst.start
    if start is None and end is not None:
        duration = fields.get(spec.duration_field) if spec.duration_field else None
        try:
            start = end - max(0.0, float(duration)) / 1000.0 if duration is not None else end
        except (TypeError, ValueError):
            start = end
    if end is None:
        report.unfinished += 1
    parent_value = fields.get(spec.parent) if spec.parent else None
    parent_key = str(parent_value) if parent_value is not None else None
    parent_id = ids.get(parent_key) if parent_key and parent_key != inst.key else None
    name = str(fields.get(spec.name_field) or "") if spec.name_field else ""
    span_type = str(fields.get(spec.type_field) or "") if spec.type_field else ""
    status = "unset" if end is None else "ok"
    if spec.status_field and fields.get(spec.status_field) is not None:
        status = _STATUS.get(str(fields.get(spec.status_field)).lower(), status)
    attributes = {}
    for attribute, source in spec.attribute_fields().items():
        value = _attr_value(fields.get(source))
        if value is not None:
            attributes[attribute[:128]] = value
    span: dict[str, Any] = {
        "id": ids[inst.key],
        "parent_id": parent_id,
        "name": (name or spec.name or spec.end)[:255],
        "span_type": (span_type or spec.type)[:50],
        "start_time": start,
        "end_time": end,
        "status": status,
        "attributes": attributes or None,
    }
    if spec.content:
        for side in ("input", "output"):
            source = getattr(spec.content, side)
            if source and fields.get(source) is not None:
                span[side] = {"value": fields.get(source)}
    return span


def read_jsonl(lines: Any) -> tuple[list[Any], int]:
    """Events from JSON Lines, and how many lines were not JSON (blank lines are skipped)."""
    events: list[Any] = []
    bad = 0
    for line in lines:
        text = line.strip() if isinstance(line, str) else line.decode("utf-8", "replace").strip()
        if not text:
            continue
        try:
            events.append(json.loads(text))
        except json.JSONDecodeError:
            bad += 1
    return events, bad


def group_by_trace(mapping: EventMapping, events: list[Any], max_events: int) -> list[list[Any]]:
    """Batches of at most ``max_events`` that never split a trace (a start and its end must
    arrive together); a trace larger than one batch gets a batch of its own."""
    groups: OrderedDict[str, list[Any]] = OrderedDict()
    for event in events:
        key = str(event.get(mapping.trace.key)) if isinstance(event, dict) else ""
        groups.setdefault(key, []).append(event)
    batches: list[list[Any]] = []
    current: list[Any] = []
    for group in groups.values():
        if current and len(current) + len(group) > max_events:
            batches.append(current)
            current = []
        current.extend(group)
    if current:
        batches.append(current)
    return batches
