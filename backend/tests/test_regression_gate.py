import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport

from app.main import app
from app.models import Span, Trace
from app.ops.regression_gate import (
    Config,
    GateError,
    Thresholds,
    build_parser,
    config_from_args,
    evaluate,
    run_gate,
)


def _summary(passed=0, failed=0, unknown=0, health=1.0):
    decided = passed + failed
    return {
        "pass": passed,
        "fail": failed,
        "unknown": unknown,
        "cases": passed + failed + unknown,
        "pass_rate": passed / decided if decided else None,
        "avg_health": health,
    }


def _cand(**kw):
    return {"summary": _summary(**kw)}


def _cmp(regressed=0, improved=0, base_health=1.0, cand_health=1.0):
    return {
        "summary": {
            "regressed": regressed,
            "improved": improved,
            "same": 0,
            "unknown": 0,
            "missing": 0,
        },
        "cases": [{"item_id": f"case{i:04d}x", "change": "regressed"} for i in range(regressed)],
        "base": {"summary": {"avg_health": base_health}},
        "candidate": {"summary": {"avg_health": cand_health}},
    }


def test_clean_run_passes_and_failed_case_blocks_without_a_baseline():
    assert evaluate(_cand(passed=3), None, Thresholds()).passed
    verdict = evaluate(_cand(passed=2, failed=1), None, Thresholds())
    assert not verdict.passed and "1 failed cases" in verdict.failures[0]
    assert evaluate(_cand(passed=2, failed=1), None, Thresholds(max_failures=1)).passed


def test_a_gate_never_passes_by_verifying_nothing():
    verdict = evaluate(_cand(unknown=4), None, Thresholds())
    assert not verdict.passed and "no case could be verified" in verdict.failures[0]
    assert evaluate(_cand(unknown=4), None, Thresholds(allow_unverified=True)).passed


def test_unknown_is_a_note_unless_it_is_made_a_failure():
    ok = evaluate(_cand(passed=2, unknown=1), None, Thresholds())
    assert ok.passed and any("unknown" in n for n in ok.notes)
    strict = evaluate(_cand(passed=2, unknown=1), None, Thresholds(fail_on_unknown=True))
    assert not strict.passed


def test_baseline_regressions_pass_rate_and_health_drop():
    assert evaluate(_cand(passed=3), _cmp(), Thresholds()).passed
    # failures are allowed to exist with a baseline; only regressions count by default
    assert evaluate(_cand(passed=2, failed=1), _cmp(), Thresholds()).passed
    bad = evaluate(_cand(passed=2, failed=1), _cmp(regressed=1), Thresholds())
    assert not bad.passed and "regressed" in bad.failures[0]
    assert evaluate(
        _cand(passed=2, failed=1), _cmp(regressed=1), Thresholds(max_regressions=1)
    ).passed
    low = evaluate(_cand(passed=1, failed=1), _cmp(), Thresholds(min_pass_rate=0.8))
    assert not low.passed and "pass rate" in low.failures[0]
    drop = evaluate(
        _cand(passed=2), _cmp(base_health=0.95, cand_health=0.7), Thresholds(max_health_drop=0.1)
    )
    assert not drop.passed and "health fell" in drop.failures[0]


def test_parser_builds_config_and_rejects_bad_config_pairs(monkeypatch):
    monkeypatch.setenv("OBSERVATORY_API_KEY", "k")
    args = build_parser().parse_args(
        ["--dataset", "d", "--config", "temp=0.2", "--max-regressions", "2", "--fail-on-unknown"]
    )
    cfg = config_from_args(args)
    assert cfg.api_key == "k" and cfg.config == {"temp": "0.2"}
    assert cfg.limits.max_regressions == 2 and cfg.limits.fail_on_unknown
    with pytest.raises(Exception):
        config_from_args(build_parser().parse_args(["--dataset", "d", "--config", "oops"]))


async def _trace(db, *, degraded=False, duration_ms=1000):
    created = datetime.now(timezone.utc) - timedelta(minutes=5)
    trace = Trace(
        name="manitos.turn",
        start_time=created,
        end_time=created + timedelta(milliseconds=duration_ms),
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
            attributes={"duration_ms": duration_ms, "llm_degraded": degraded},
        )
    )
    await db.commit()
    return trace


@pytest.mark.anyio
async def test_end_to_end_records_a_run_from_results_and_gates_on_the_baseline(
    client, db_session, tmp_path
):
    source = await _trace(db_session)
    ds = (await client.post("/v1/datasets", json={"name": "ci"})).json()["id"]
    item = (
        await client.post(
            f"/v1/datasets/{ds}/items",
            json={"trace_id": source.id, "expect": {"health": {"no_degradation": True}}},
        )
    ).json()
    good, bad = await _trace(db_session), await _trace(db_session, degraded=True)
    transport = ASGITransport(app=app)

    def results(trace):
        path = tmp_path / f"{trace.id}.json"
        # the case is identified by the trace it was saved from
        path.write_text(json.dumps([{"case_trace": source.id, "trace_id": trace.id}]))
        return str(path)

    first = await run_gate(
        Config(
            url="http://test", dataset="ci", results_path=results(good), name="v1", commit="abc"
        ),
        transport=transport,
    )
    assert first["passed"] and first["candidate"]["version"]["commit"] == "abc"

    second = await run_gate(
        Config(
            url="http://test",
            dataset="ci",
            results_path=results(bad),
            name="v2",
            baseline="previous",
        ),
        transport=transport,
    )
    assert not second["passed"] and second["comparison"]["regressed"] == 1
    assert second["baseline"]["name"] == "v1"
    assert item["id"]

    # Re-gating the recorded latest run needs no results file.
    again = await run_gate(
        Config(url="http://test", dataset="ci", candidate="latest", baseline="previous"),
        transport=transport,
    )
    assert not again["passed"]


@pytest.mark.anyio
async def test_gate_errors_are_reported_not_passed(client, db_session, tmp_path):
    transport = ASGITransport(app=app)
    with pytest.raises(GateError, match="dataset not found"):
        await run_gate(Config(url="http://test", dataset="missing"), transport=transport)
    await client.post("/v1/datasets", json={"name": "empty"})
    with pytest.raises(GateError, match="no runs"):
        await run_gate(Config(url="http://test", dataset="empty"), transport=transport)
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(GateError, match="non-empty JSON list"):
        await run_gate(
            Config(url="http://test", dataset="empty", results_path=str(bad)), transport=transport
        )


def test_main_exits_2_when_observer_is_unreachable(capsys):
    from app.ops.regression_gate import main

    assert main(["--dataset", "x", "--url", "http://127.0.0.1:1", "--report", ""]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] is None and "unreachable" in out["error"]
    assert main(["--dataset", "x", "--config", "oops"]) == 2
