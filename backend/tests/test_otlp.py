import gzip
import json

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from sqlalchemy import select

from app.models import Span, Trace
from app.otlp import OtlpError, decompress, span_uuid, trace_uuid, translate

TID = "5b8efff798038103d269b633813fc60c"
ROOT = "eee19b7ec3c1b174"
CHILD = "eee19b7ec3c1b175"
T0 = 1_760_000_000_000_000_000


def _attr(key, value):
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": value}}


def _span(sid, name, *, parent="", start=0, ms=1000, status=1, attrs=None, tid=TID):
    return {
        "traceId": tid,
        "spanId": sid,
        "parentSpanId": parent,
        "name": name,
        "startTimeUnixNano": str(T0 + start),
        "endTimeUnixNano": str(T0 + start + ms * 1_000_000),
        "status": {"code": status},
        "attributes": [_attr(k, v) for k, v in (attrs or {}).items()],
    }


def _request(spans, service="my-agent", env="prod", extra_resource=None):
    resource = [_attr("service.name", service), _attr("deployment.environment.name", env)]
    resource += [_attr(k, v) for k, v in (extra_resource or {}).items()]
    return {
        "resourceSpans": [{"resource": {"attributes": resource}, "scopeSpans": [{"spans": spans}]}]
    }


def _demo():
    return _request(
        [
            _span(ROOT, "agent.run", attrs={"gen_ai.operation.name": "invoke_agent"}),
            _span(
                CHILD,
                "chat gpt",
                parent=ROOT,
                start=10_000_000,
                attrs={
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": "gpt-x",
                    "gen_ai.usage.input_tokens": 120,
                    "gen_ai.usage.output_tokens": 30,
                    "gen_ai.prompt": "PRIVATE PROMPT",
                    "gen_ai.completion": "PRIVATE REPLY",
                    "http.url": "https://x.test/?token=SECRET",
                },
            ),
            _span(
                "eee19b7ec3c1b176",
                "tool call",
                parent=ROOT,
                start=20_000_000,
                status=2,
                attrs={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "web_search"},
            ),
        ]
    )


def test_translation_maps_types_status_ids_and_drops_content():
    out = translate(_demo(), mode="metadata")
    assert out.rejected == 0 and len(out.envelopes) == 1
    env = out.envelopes[0]
    assert env["project_id"] == "my-agent" and env["environment"] == "prod"
    assert env["trace"]["id"] == trace_uuid(TID) and env["trace"]["name"] == "agent.run"
    assert env["trace"]["status"] == "error"  # one span failed
    by_name = {s["name"]: s for s in env["spans"]}
    assert by_name["agent.run"]["span_type"] == "agent"
    llm = by_name["chat gpt"]
    assert llm["span_type"] == "llm" and llm["tokens_input"] == 120 and llm["tokens_output"] == 30
    assert llm["attributes"]["model"] == "gpt-x"
    assert llm["parent_id"] == span_uuid(TID, ROOT)
    tool = by_name["web_search"]  # a tool span is named after the tool, like Manitos' own
    assert tool["span_type"] == "tool" and tool["status"] == "error"
    blob = json.dumps(env)
    for secret in ("PRIVATE", "SECRET"):
        assert secret not in blob
    assert "gen_ai.usage.input_tokens" in llm["attributes"]  # counts are not content


def test_full_capture_keeps_content_attributes():
    env = translate(_demo(), mode="full").envelopes[0]
    assert "PRIVATE PROMPT" in json.dumps(env)


def test_invalid_spans_are_rejected_not_fatal():
    bad = _request([_span("zz", "x"), _span(ROOT, "ok")])
    out = translate(bad)
    assert out.rejected == 1 and len(out.envelopes[0]["spans"]) == 1


def test_decompress_gzip_and_limits():
    data = b'{"a":1}'
    assert decompress(gzip.compress(data), "gzip") == data
    with pytest.raises(OtlpError, match="unsupported"):
        decompress(data, "br")
    with pytest.raises(OtlpError, match="invalid compressed"):
        decompress(b"not gzip", "gzip")
    import app.otlp as otlp

    old = otlp.MAX_BODY_BYTES
    otlp.MAX_BODY_BYTES = 100
    try:
        with pytest.raises(OtlpError) as exc:
            decompress(gzip.compress(b"0" * 10_000), "gzip")
        assert exc.value.status == 413
    finally:
        otlp.MAX_BODY_BYTES = old


def _post(client, body, *, ctype="application/json", headers=None):
    return client.post(
        "/v1/otlp/v1/traces",
        content=body,
        headers={"content-type": ctype, **(headers or {})},
    )


@pytest.mark.anyio
async def test_json_ingestion_stores_trace_and_spans_and_is_idempotent(client, db_session):
    body = json.dumps(_demo())
    first = await _post(client, body)
    assert first.status_code == 200 and first.json() == {}
    again = await _post(client, body)  # an exporter retry
    assert again.status_code == 200
    trace = await db_session.get(Trace, trace_uuid(TID))
    assert trace.project_id == "my-agent" and trace.environment == "prod"
    spans = (
        (await db_session.execute(select(Span).where(Span.trace_id == trace_uuid(TID))))
        .scalars()
        .all()
    )
    assert len(spans) == 3  # not duplicated by the retry
    tool = next(s for s in spans if s.span_type == "tool")
    assert tool.name == "web_search" and tool.status == "error"
    child = next(s for s in spans if s.name == "chat gpt")
    assert child.parent_span_id == span_uuid(TID, ROOT)


@pytest.mark.anyio
async def test_protobuf_gzip_ingestion_and_protobuf_response(client, db_session):
    message = ExportTraceServiceRequest()
    rs = message.resource_spans.add()
    kv = rs.resource.attributes.add()
    kv.key, kv.value.string_value = "service.name", "pb-agent"
    span = rs.scope_spans.add().spans.add()
    span.trace_id = bytes.fromhex(TID)
    span.span_id = bytes.fromhex(ROOT)
    span.name = "pb.root"
    span.start_time_unix_nano = T0
    span.end_time_unix_nano = T0 + 5_000_000
    span.status.code = 1
    attr = span.attributes.add()
    attr.key, attr.value.string_value = "gen_ai.operation.name", "chat"
    res = await _post(
        client,
        gzip.compress(message.SerializeToString()),
        ctype="application/x-protobuf",
        headers={"content-encoding": "gzip"},
    )
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/x-p")
    assert ExportTraceServiceResponse.FromString(res.content).partial_success.rejected_spans == 0
    stored = await db_session.get(Trace, trace_uuid(TID))
    assert stored.project_id == "pb-agent" and stored.name == "pb.root"


@pytest.mark.anyio
async def test_spans_arriving_in_later_batches_link_and_keep_the_full_range(client, db_session):
    late_child = _request([_span(CHILD, "child", parent=ROOT, start=3_000_000_000, ms=500)])
    root = _request([_span(ROOT, "root", start=0, ms=100)])
    assert (await _post(client, json.dumps(late_child))).status_code == 200  # parent not seen yet
    assert (await _post(client, json.dumps(root))).status_code == 200
    trace = await db_session.get(Trace, trace_uuid(TID))
    assert trace.name  # a name, from whichever batch came first
    spans = (
        (await db_session.execute(select(Span).where(Span.trace_id == trace_uuid(TID))))
        .scalars()
        .all()
    )
    assert len(spans) == 2
    # Both batches belong to one trace spanning from the root's start to the child's end.
    span = (trace.end_time - trace.start_time).total_seconds()
    assert span == pytest.approx(3.5, abs=0.01)
    third = _request([_span("eee19b7ec3c1b177", "grandchild", parent=CHILD, start=3_100_000_000)])
    await _post(client, json.dumps(third))
    gc = (await db_session.execute(select(Span).where(Span.name == "grandchild"))).scalar_one()
    assert gc.parent_span_id == span_uuid(TID, CHILD)  # linked to a parent from an earlier batch


@pytest.mark.anyio
async def test_project_header_and_http_errors(client, db_session):
    body = json.dumps(_demo())
    ok = await _post(client, body, headers={"x-observer-project": "team-a"})
    assert ok.status_code == 200
    assert (await db_session.get(Trace, trace_uuid(TID))).project_id == "team-a"
    assert (await _post(client, body, ctype="text/plain")).status_code == 415
    assert (await _post(client, "{nope")).status_code == 400
    assert (await _post(client, "[1]")).status_code == 400
    assert (await _post(client, b"\xff\xff", ctype="application/x-protobuf")).status_code == 400
    partial = await _post(client, json.dumps(_request([_span("zz", "bad")])))
    assert partial.status_code == 200
    assert partial.json()["partialSuccess"]["rejectedSpans"] == "1"


@pytest.mark.anyio
async def test_a_trace_owned_by_another_project_is_rejected_without_a_receipt(client, db_session):
    body = json.dumps(_demo())
    assert (await _post(client, body, headers={"x-observer-project": "a"})).status_code == 200
    res = await _post(
        client, body.replace("agent.run", "agent.run2"), headers={"x-observer-project": "b"}
    )
    assert res.status_code == 200
    assert res.json()["partialSuccess"]["rejectedSpans"] == "3"


@pytest.mark.anyio
async def test_a_child_exported_before_its_parent_keeps_the_parent(client, db_session):
    """OpenTelemetry exports a child when it ends, before its parent: the receiver used to drop a
    parent it had not stored yet, flattening the trace."""
    child = _request([_span(CHILD, "child", parent=ROOT, start=10_000_000, ms=50)])
    assert (await _post(client, json.dumps(child))).status_code == 200
    stored = (await db_session.execute(select(Span).where(Span.name == "child"))).scalar_one()
    assert stored.parent_span_id == span_uuid(TID, ROOT)
    assert (await db_session.get(Trace, trace_uuid(TID))).status == "unset"  # root not seen yet
    root = _request([_span(ROOT, "agent.run", start=0, ms=500)])
    assert (await _post(client, json.dumps(root))).status_code == 200
    trace = await db_session.get(Trace, trace_uuid(TID))
    await db_session.refresh(trace)
    assert trace.name == "agent.run" and trace.status == "ok"


@pytest.mark.anyio
async def test_a_failed_root_stays_failed_when_a_later_batch_is_all_ok(client, db_session):
    root = _request([_span(ROOT, "agent.run", start=0, ms=500, status=2)])
    late = _request([_span(CHILD, "late", parent=ROOT, start=10_000_000, ms=50, status=1)])
    assert (await _post(client, json.dumps(root))).status_code == 200
    assert (await _post(client, json.dumps(late))).status_code == 200
    trace = await db_session.get(Trace, trace_uuid(TID))
    await db_session.refresh(trace)
    assert trace.status == "error"
