"""OTLP/HTTP trace receiver: translate standard OpenTelemetry spans into Observer envelopes.

Any app instrumented with a stock OpenTelemetry exporter can send here (JSON or protobuf,
optionally gzipped) without the Observer SDK. The translation produces the same
``manitos.telemetry.v1`` envelopes the Manitos exporter sends, so ingestion, idempotency and
the dashboard are shared. Mapping reference: ``docs/otlp.md``.

Privacy: Observer is metadata-only by default. In ``metadata`` capture mode (the default,
``OBSERVATORY_OTLP_CAPTURE``) attributes that can carry prompts, replies or other content are
dropped before storage; ``full`` keeps them.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import uuid
import zlib
from dataclasses import dataclass
from typing import Any

from app.capture import MODES, filter_attributes

MAX_BODY_BYTES = 16 * 1024 * 1024
_NAMESPACE = uuid.UUID("0b3f6d1e-6a54-4c63-9a52-7d2f5d9c1a77")
_MAX_ATTRS = 64
_SPANS_PER_ENVELOPE = 500

_GEN_AI_OPERATION_TYPE = {
    "chat": "llm",
    "text_completion": "llm",
    "generate_content": "llm",
    "completion": "llm",
    "embeddings": "embedding",
    "execute_tool": "tool",
    "invoke_agent": "agent",
    "create_agent": "agent",
}
_OPENINFERENCE_KIND = {
    "LLM": "llm",
    "TOOL": "tool",
    "AGENT": "agent",
    "CHAIN": "chain",
    "RETRIEVER": "retriever",
    "EMBEDDING": "embedding",
    "RERANKER": "reranker",
}


class OtlpError(ValueError):
    """The request cannot be decoded (answered 400/413/415)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def capture_mode() -> str:
    """OTLP's default capture mode (a project's own setting overrides it at ingestion)."""
    value = os.getenv("OBSERVATORY_OTLP_CAPTURE", "metadata").strip().lower()
    return value if value in MODES else "metadata"


def decompress(body: bytes, encoding: str | None) -> bytes:
    """Undo ``Content-Encoding`` with a cap on the decompressed size (zip-bomb guard)."""
    encoding = (encoding or "").strip().lower()
    if encoding in {"", "identity"}:
        data = body
    elif encoding in {"gzip", "x-gzip"}:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = _bounded_decompress(decoder, body)
    elif encoding == "deflate":
        data = _bounded_decompress(zlib.decompressobj(), body)
    else:
        raise OtlpError(f"unsupported Content-Encoding: {encoding}", 415)
    if len(data) > MAX_BODY_BYTES:
        raise OtlpError("request too large", 413)
    return data


def _bounded_decompress(decoder: Any, body: bytes) -> bytes:
    try:
        data = decoder.decompress(body, MAX_BODY_BYTES + 1)
    except (zlib.error, EOFError, gzip.BadGzipFile) as exc:
        raise OtlpError("invalid compressed body") from exc
    if decoder.unconsumed_tail:
        raise OtlpError("request too large", 413)
    return data


def _hex_id(value: Any) -> str:
    """OTLP/JSON ids are hex strings; protobuf ids are bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value or "").strip().lower()


def trace_uuid(trace_id_hex: str) -> str:
    """The 16-byte trace id as a UUID (a bijection, so a trace keeps one identity)."""
    return str(uuid.UUID(hex=trace_id_hex.rjust(32, "0")[-32:]))


def span_uuid(trace_id_hex: str, span_id_hex: str) -> str:
    """Span ids are 8 bytes: a stable derived UUID, so a re-sent span upserts, not duplicates."""
    return str(uuid.uuid5(_NAMESPACE, f"{trace_id_hex}:{span_id_hex}"))


def _any_value(value: dict[str, Any]) -> Any:
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return None
    if "arrayValue" in value:
        items = [_any_value(v) for v in (value["arrayValue"] or {}).get("values", [])]
        return items if all(isinstance(i, (str, int, float, bool)) for i in items) else None
    return None


def _attrs(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        key = str(item.get("key") or "")
        if key:
            value = _any_value(item.get("value") or {})
            if value is not None:
                out[key] = value
    return out


def _safe_attributes(raw: dict[str, Any], mode: str) -> dict[str, Any]:
    """Bound the attributes; ``metadata`` also drops content-like keys (shared with capture.py).

    The receiver translates with ``full`` and lets ingestion apply the project's capture mode."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if len(out) >= _MAX_ATTRS:
            break
        if isinstance(value, str):
            value = value[:4000]
        elif isinstance(value, list):
            value = [v[:4000] if isinstance(v, str) else v for v in value[:16]]
        out[key[:128]] = value
    return filter_attributes(out, mode) or {}


def _span_type(attrs: dict[str, Any]) -> str:
    kind = str(attrs.get("openinference.span.kind") or "").upper()
    if kind in _OPENINFERENCE_KIND:
        return _OPENINFERENCE_KIND[kind]
    operation = str(attrs.get("gen_ai.operation.name") or "").lower()
    if operation in _GEN_AI_OPERATION_TYPE:
        return _GEN_AI_OPERATION_TYPE[operation]
    if attrs.get("gen_ai.tool.name"):
        return "tool"
    return "generic"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _status(span: dict[str, Any]) -> str:
    code = (span.get("status") or {}).get("code", 0)
    if code in (2, "STATUS_CODE_ERROR"):
        return "error"
    if code in (1, "STATUS_CODE_OK"):
        return "ok"
    return "unset"


def _seconds(nanos: Any) -> float | None:
    try:
        value = int(nanos)
    except (TypeError, ValueError):
        return None
    return value / 1e9 if value > 0 else None


@dataclass
class Translated:
    """Envelopes ready for ingestion plus the spans that could not be translated."""

    envelopes: list[dict[str, Any]]
    rejected: int
    errors: list[str]


def translate(
    request: dict[str, Any],
    *,
    project_id: str | None = None,
    mode: str | None = None,
) -> Translated:
    """OTLP ``ExportTraceServiceRequest`` (JSON-shaped dict) -> telemetry envelopes."""
    mode = mode or capture_mode()
    per_trace: dict[str, dict[str, Any]] = {}
    rejected = 0
    errors: list[str] = []
    for resource_spans in request.get("resourceSpans") or []:
        resource = _attrs((resource_spans.get("resource") or {}).get("attributes"))
        service = str(resource.get("service.name") or "otlp")[:128] or "otlp"
        environment = str(
            resource.get("deployment.environment.name")
            or resource.get("deployment.environment")
            or "default"
        )[:64]
        instance = str(resource.get("service.instance.id") or service)[:128]
        scopes = resource_spans.get("scopeSpans") or resource_spans.get(
            "instrumentationLibrarySpans"
        )
        for scope_spans in scopes or []:
            for span in scope_spans.get("spans") or []:
                tid = _hex_id(span.get("traceId"))
                sid = _hex_id(span.get("spanId"))
                start = _seconds(span.get("startTimeUnixNano"))
                end = _seconds(span.get("endTimeUnixNano"))
                if (
                    not re.fullmatch(r"[0-9a-f]{1,32}", tid)
                    or not re.fullmatch(r"[0-9a-f]{1,16}", sid)
                    or start is None
                ):
                    rejected += 1
                    errors.append("span with an invalid trace id, span id or start time")
                    continue
                if end is not None and end < start:
                    end = start
                raw = _attrs(span.get("attributes"))
                attrs = _safe_attributes(raw, mode)
                model = raw.get("gen_ai.response.model") or raw.get("gen_ai.request.model")
                if model:
                    attrs["model"] = str(model)[:160]
                span_type = _span_type(raw)
                name = str(span.get("name") or "span")
                if span_type == "tool" and raw.get("gen_ai.tool.name"):
                    name = str(raw["gen_ai.tool.name"])
                tokens_in = _number(
                    raw.get("gen_ai.usage.input_tokens", raw.get("gen_ai.usage.prompt_tokens"))
                )
                tokens_out = _number(
                    raw.get("gen_ai.usage.output_tokens", raw.get("gen_ai.usage.completion_tokens"))
                )
                parent = _hex_id(span.get("parentSpanId"))
                item = {
                    "id": span_uuid(tid, sid),
                    "_parent": span_uuid(tid, parent) if parent.strip("0") else None,
                    "name": name[:255] or "span",
                    "span_type": span_type,
                    "start_time": start,
                    "end_time": end,
                    "status": _status(span),
                    "attributes": attrs or None,
                    "tokens_input": int(tokens_in) if tokens_in is not None else None,
                    "tokens_output": int(tokens_out) if tokens_out is not None else None,
                    "_root": not parent.strip("0"),
                }
                bucket = per_trace.setdefault(
                    tid,
                    {
                        "project": (project_id or service)[:128],
                        "environment": environment,
                        "instance": instance,
                        "session": str(
                            raw.get("session.id") or raw.get("gen_ai.conversation.id") or tid
                        )[:255],
                        "spans": {},
                    },
                )
                bucket["spans"][item["id"]] = item
    envelopes: list[dict[str, Any]] = []
    for tid, bucket in per_trace.items():
        items = list(bucket["spans"].values())
        roots = [i for i in items if i["_root"]]
        head = min(roots or items, key=lambda i: i["start_time"])
        starts = [i["start_time"] for i in items]
        ends = [i["end_time"] for i in items if i["end_time"] is not None]
        trace_status = (
            "error"
            if any(i["status"] == "error" for i in items)
            else ("ok" if all(i["status"] == "ok" for i in items) else "unset")
        )
        for chunk_start in range(0, len(items), _SPANS_PER_ENVELOPE):
            chunk = items[chunk_start : chunk_start + _SPANS_PER_ENVELOPE]
            spans = []
            for i in chunk:
                clean = {k: v for k, v in i.items() if not k.startswith("_") and v is not None}
                # Kept even when the parent is not in this request: a child is exported before
                # its parent ends, and a parent may live in another service.
                clean["parent_id"] = i["_parent"]
                spans.append(clean)
            body = {
                "schema_version": "manitos.telemetry.v1",
                "project_id": bucket["project"],
                "environment": bucket["environment"],
                "service_instance_id": bucket["instance"],
                "session_id": bucket["session"],
                "turn_id": tid[:128],
                "actor_id_hash": None,
                "trace": {
                    "id": trace_uuid(tid),
                    "name": head["name"],
                    "start_time": min(starts),
                    "end_time": max(ends) if ends else None,
                    "status": trace_status,
                },
                "spans": spans,
            }
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, default=str).encode()
            ).hexdigest()
            body["idempotency_key"] = f"otlp:{digest[:48]}"
            envelopes.append(body)
    return Translated(envelopes=envelopes, rejected=rejected, errors=errors[:5])


def request_from_protobuf(data: bytes) -> dict[str, Any]:
    """Decode a protobuf ``ExportTraceServiceRequest`` into the OTLP/JSON shape."""
    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )
    except ImportError as exc:  # declared dependency; this is a broken install
        raise OtlpError("protobuf support is not installed", 415) from exc
    message = ExportTraceServiceRequest()
    try:
        message.ParseFromString(data)
    except Exception as exc:  # protobuf raises DecodeError and others
        raise OtlpError("invalid protobuf body") from exc
    data = MessageToDict(message, preserving_proto_field_name=False)
    # protobuf's JSON mapping writes bytes as base64; OTLP/JSON (and this module) use hex ids.
    for resource_spans in data.get("resourceSpans", []):
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                for key in ("traceId", "spanId", "parentSpanId"):
                    if span.get(key):
                        span[key] = base64.b64decode(span[key]).hex()
    return data


def request_from_json(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise OtlpError("invalid JSON body") from exc
    if not isinstance(value, dict):
        raise OtlpError("body must be a JSON object")
    return value


def response_body(rejected: int, message: str, *, protobuf: bool) -> tuple[bytes, str]:
    """``ExportTraceServiceResponse`` in the format the exporter used."""
    if protobuf:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceResponse,
        )

        response = ExportTraceServiceResponse()
        if rejected:
            response.partial_success.rejected_spans = rejected
            response.partial_success.error_message = message[:500]
        return response.SerializeToString(), "application/x-protobuf"
    payload: dict[str, Any] = {}
    if rejected:
        payload["partialSuccess"] = {"rejectedSpans": str(rejected), "errorMessage": message[:500]}
    return json.dumps(payload).encode(), "application/json"
