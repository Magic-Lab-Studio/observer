"""observer-replay (step 12): run cases N times against an agent endpoint and record the runs."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport

from app.main import app
from app.models import Span, Trace
from app.ops.replay import (
    Config,
    ReplayError,
    build_parser,
    config_from_args,
    load_cases,
    main,
    run,
)


async def _trace(db, *, degraded=False):
    created = datetime.now(timezone.utc) - timedelta(minutes=5)
    trace = Trace(
        name="manitos.turn",
        start_time=created,
        end_time=created + timedelta(seconds=1),
        status="ok",
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
            status="ok",
            attributes={"duration_ms": 1000, "llm_degraded": degraded},
        )
    )
    await db.commit()
    return trace


def _agent(trace_ids, seen):
    """A fake agent: answers each call with the next trace id, records what it was sent."""
    queue = list(trace_ids)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"reply": "ok", "meta": {"trace": queue.pop(0)}})

    return httpx.MockTransport(handler)


def test_cases_without_an_input_are_refused_by_name(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"id": "ds1", "items": [{"id": "a", "input": "hola"}, {"id": "b"}]}))
    with pytest.raises(ReplayError, match="have no input.*'b'"):
        load_cases(str(path))


def test_an_export_gives_the_dataset_id_and_a_plain_list_takes_case_traces(tmp_path):
    export = tmp_path / "export.json"
    export.write_text(
        json.dumps({"id": "ds1", "items": [{"id": "a", "source_trace_id": "t", "input": "x"}]})
    )
    cases, dataset = load_cases(str(export))
    assert dataset == "ds1" and cases[0].item_id == "a"
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps([{"case_trace": "t", "input": "x"}]))
    cases, dataset = load_cases(str(plain))
    assert dataset == "" and cases[0].item_id == "" and cases[0].case_trace == "t"


def test_repeat_is_bounded():
    args = build_parser().parse_args(["--cases", "c", "--endpoint", "http://a", "--repeat", "100"])
    with pytest.raises(ReplayError, match="between 1 and 99"):
        config_from_args(args)


@pytest.mark.anyio
async def test_a_case_right_three_times_out_of_five_is_recorded_as_unstable(
    client, db_session, tmp_path
):
    source = await _trace(db_session)
    ds = (await client.post("/v1/datasets", json={"name": "flaky"})).json()["id"]
    item = (
        await client.post(
            f"/v1/datasets/{ds}/items",
            json={"trace_id": source.id, "expect": {"health": {"no_degradation": True}}},
        )
    ).json()
    runs = [await _trace(db_session, degraded=bad) for bad in (False, True, False, True, False)]
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps({"id": ds, "items": [{"id": item["id"], "input": "¿qué hora es?"}]})
    )
    out = tmp_path / "results.json"
    seen: list = []
    config = Config(
        cases_path=str(cases),
        endpoint="http://agent/turn",
        repeat=5,
        out_path=str(out),
        trace_field="meta.trace",
        record=True,
        name="nightly",
    )

    code, report = await run(
        config,
        transport=_agent([t.id for t in runs], seen),
        observer_transport=ASGITransport(app=app),
    )

    assert code == 0 and report["calls"] == 5 and report["traced"] == 5
    assert [s["repetition"] for s in seen] == [0, 1, 2, 3, 4] and seen[0][
        "input"
    ] == "¿qué hora es?"
    summary = report["run"]["summary"]
    assert summary["unstable"] == 1 and summary["pass"] == 0 and summary["fail"] == 0
    (case,) = report["unstable"]
    assert case["distribution"] == {"pass": 3, "fail": 2, "unknown": 0}
    written = json.loads(out.read_text())
    assert {r["item_id"] for r in written} == {item["id"]} and len(
        written
    ) == 5  # observer-regression --results


@pytest.mark.anyio
async def test_a_deterministic_case_is_not_unstable(client, db_session, tmp_path):
    source = await _trace(db_session)
    ds = (await client.post("/v1/datasets", json={"name": "steady"})).json()["id"]
    item = (
        await client.post(
            f"/v1/datasets/{ds}/items",
            json={"trace_id": source.id, "expect": {"health": {"no_degradation": True}}},
        )
    ).json()
    runs = [await _trace(db_session) for _ in range(3)]
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"id": ds, "items": [{"id": item["id"], "input": "hola"}]}))
    config = Config(
        cases_path=str(cases),
        endpoint="http://agent/turn",
        repeat=3,
        trace_field="meta.trace",
        record=True,
    )
    code, report = await run(
        config,
        transport=_agent([t.id for t in runs], []),
        observer_transport=ASGITransport(app=app),
    )
    assert code == 0 and report["run"]["summary"]["pass"] == 1 and report["unstable"] == []


@pytest.mark.anyio
async def test_failed_calls_are_reported_and_the_rest_still_written(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"item_id": "a", "input": "x"}]))
    calls = iter([httpx.Response(200, headers={"X-Observer-Trace-Id": "t1"}), httpx.Response(503)])
    transport = httpx.MockTransport(lambda request: next(calls))
    out = tmp_path / "r.json"
    code, report = await run(
        Config(cases_path=str(cases), endpoint="http://agent", repeat=2, out_path=str(out)),
        transport=transport,
    )
    assert code == 1 and report["traced"] == 1
    assert report["failed"] == [{"item_id": "a", "repetition": 1, "error": "HTTP 503"}]
    assert json.loads(out.read_text()) == [{"item_id": "a", "repetition": 0, "trace_id": "t1"}]


@pytest.mark.anyio
async def test_nothing_traced_is_exit_two(tmp_path):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"item_id": "a", "input": "x"}]))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"reply": "no trace"}))
    with pytest.raises(ReplayError, match="no call returned a trace id"):
        await run(Config(cases_path=str(cases), endpoint="http://agent"), transport=transport)


def test_main_reports_bad_arguments_with_exit_two(tmp_path, capsys):
    assert main(["--cases", str(tmp_path / "missing.json"), "--endpoint", "http://agent"]) == 2
    assert "cannot read --cases" in capsys.readouterr().err
