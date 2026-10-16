import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport
from mcp.shared.memory import create_connected_server_and_client_session

from app.main import app
from app.models import Evaluation, Span, Trace
from app.ops import mcp_server
from app.ops.mcp_server import ObserverReader, ObserverReadError, build_server, trace_detail


async def _trace(db, *, status="ok", degraded=False, ms=1500):
    created = datetime.now(timezone.utc) - timedelta(minutes=5)
    trace = Trace(
        name="manitos.turn",
        start_time=created,
        end_time=created + timedelta(milliseconds=ms),
        status=status,
        project_id="manitos",
        environment="test",
        created_at=created,
    )
    db.add(trace)
    await db.flush()
    db.add(
        Span(
            trace_id=trace.id,
            name="manitos.turn.lifecycle",
            span_type="turn",
            start_time=created,
            end_time=created + timedelta(milliseconds=ms),
            status=status,
            inp={"text": "PRIVATE QUESTION"},
            out={"text": "PRIVATE ANSWER"},
            attributes={"duration_ms": ms, "llm_degraded": degraded},
        )
    )
    db.add(
        Span(
            trace_id=trace.id,
            name="llm.chain",
            span_type="event",
            start_time=created,
            status="ok",
            attributes={"answered_by": "groq · qwen", "attempts_text": "groq:q=429>gemini:f=ok"},
        )
    )
    db.add(
        Span(
            trace_id=trace.id, name="web_search", span_type="tool", start_time=created, status="ok"
        )
    )
    await db.commit()
    return trace


async def _session(client_ready):
    reader = ObserverReader("http://test", transport=ASGITransport(app=app))
    return build_server(reader), reader


async def _call(session, name, **args):
    result = await session.call_tool(name, args)
    assert not result.isError, result.content
    (part,) = [json.loads(c.text) for c in result.content if hasattr(c, "text")]
    return part


@pytest.mark.anyio
async def test_every_tool_is_declared_read_only(client):
    server, _ = await _session(client)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        tools = (await session.list_tools()).tools
    names = {t.name for t in tools}
    assert {
        "observer_overview",
        "list_failed_traces",
        "get_trace",
        "compare_runs",
        "query_metrics",
    } <= names
    assert all(t.annotations and t.annotations.readOnlyHint for t in tools)
    assert not any(re.search(r"delete|create|update|write|save|ack|remove", n) for n in names)


@pytest.mark.anyio
async def test_get_trace_omits_content_and_shows_chain_tools_and_scores(client, db_session):
    trace = await _trace(db_session)
    db_session.add(
        Evaluation(
            trace_id=trace.id,
            evaluator_type="rule_based",
            score=0.89,
            criteria={},
            result={
                "grade": "B",
                "passed": True,
                "health": {"score": 1.0, "grade": "A"},
                "correctness": {"status": "unknown", "score": None},
                "criteria": [{"name": "latency", "score": 1.0, "detail": "1500 ms"}],
            },
        )
    )
    await db_session.commit()
    server, _ = await _session(client)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        detail = await _call(session, "get_trace", trace_id=trace.id)
    assert "PRIVATE" not in json.dumps(detail) and detail["content_included"] is False
    assert detail["provider_chain"] == "groq:q=429>gemini:f=ok"
    assert detail["answered_by"] == "groq · qwen"
    assert [t["name"] for t in detail["tools"]] == ["web_search"]
    ev = detail["evaluations"][0]
    assert ev["grade"] == "B" and ev["correctness"]["status"] == "unknown"
    assert ev["criteria"][0]["name"] == "latency"


def test_content_only_with_the_explicit_switch(monkeypatch):
    trace = {"id": "t", "name": "n", "status": "ok"}
    spans = [{"id": "s", "name": "x", "span_type": "turn", "input": {"a": "SECRET"}, "output": {}}]
    assert "SECRET" not in json.dumps(trace_detail(trace, spans, [], content=False))
    assert "SECRET" in json.dumps(trace_detail(trace, spans, [], content=True))
    monkeypatch.setenv("OBSERVER_MCP_CONTENT", "1")
    assert mcp_server.content_enabled()
    monkeypatch.delenv("OBSERVER_MCP_CONTENT")
    assert not mcp_server.content_enabled()


@pytest.mark.anyio
async def test_failed_traces_combine_errors_and_low_health(client, db_session):
    bad = await _trace(db_session, status="error")
    slow = await _trace(db_session)
    await _trace(db_session)  # healthy: must not appear
    db_session.add(
        Evaluation(
            trace_id=slow.id,
            evaluator_type="rule_based",
            score=0.3,
            criteria={},
            result={"grade": "D", "passed": False},
        )
    )
    await db_session.commit()
    server, _ = await _session(client)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        out = await _call(session, "list_failed_traces", limit=10)
    ids = {t["id"]: t for t in out["traces"]}
    assert set(ids) == {bad.id, slow.id} and out["count"] == 2
    assert ids[bad.id]["reasons"] == ["trace status is error"]
    assert "health score 0.3" in ids[slow.id]["reasons"][0]


@pytest.mark.anyio
async def test_datasets_runs_comparison_and_metrics_are_readable(client, db_session):
    source = await _trace(db_session)
    ds = (await client.post("/v1/datasets", json={"name": "d"})).json()["id"]
    item = (
        await client.post(
            f"/v1/datasets/{ds}/items",
            json={"trace_id": source.id, "expect": {"health": {"no_degradation": True}}},
        )
    ).json()["id"]
    good, bad = await _trace(db_session), await _trace(db_session, degraded=True)
    runs = []
    for trace in (good, bad):
        runs.append(
            (
                await client.post(
                    f"/v1/datasets/{ds}/runs",
                    json={"results": [{"item_id": item, "trace_id": trace.id}]},
                )
            ).json()["id"]
        )
    server, _ = await _session(client)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        assert (await _call(session, "list_datasets"))["datasets"][0]["items"] == 1
        assert len((await _call(session, "get_dataset", dataset_id=ds))["items"]) == 1
        assert len((await _call(session, "list_runs", dataset_id=ds))["runs"]) == 2
        diff = await _call(
            session, "compare_runs", dataset_id=ds, base_run_id=runs[0], candidate_run_id=runs[1]
        )
        assert diff["summary"]["regressed"] == 1
        schema = await _call(session, "metrics_schema")
        assert "turns" in schema["views"]
        rows = await _call(
            session,
            "query_metrics",
            view="turns",
            metrics=[{"measure": "count", "aggregation": "count"}],
            hours=6,
        )
        assert rows["samples"] >= 1
        overview = await _call(session, "observer_overview", hours=6)
        assert "quality" in overview and "open_alerts" in overview
        listed = await _call(session, "list_traces", status="error", limit=5)
        assert listed == {"traces": []}


@pytest.mark.anyio
async def test_errors_are_reported_cleanly(client):
    server, _ = await _session(client)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        result = await session.call_tool("get_trace", {"trace_id": "not-a-uuid"})
        assert result.isError and "400" in result.content[0].text
    down = ObserverReader("http://127.0.0.1:1")
    with pytest.raises(ObserverReadError, match="unreachable"):
        await down.get("/v1/datasets")
    await down.aclose()


def test_the_reader_can_only_read():
    source = Path(mcp_server.__file__).read_text()
    assert not re.search(r"\.(put|patch|delete)\(", source)
    methods = set(re.findall(r'_send\(\s*"(\w+)"', source))
    assert methods == {"GET", "POST"}
    # the single POST is the metrics query, which computes and writes nothing
    assert source.count('"/v1/analytics/query"') == 1
    assert not [m for m in dir(ObserverReader) if re.match(r"(delete|create|update|post|put)", m)]
