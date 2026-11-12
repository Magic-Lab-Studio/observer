"""Step 12: the same case run N times in one run; a case whose repetitions disagree is unstable."""

import pytest

from app.case_check import aggregate_repetitions, binary_entropy
from app.ops.regression_gate import Thresholds, evaluate


def _r(item, status, health=None):
    return {"item_id": item, "status": status, "health_score": health, "duration_ms": None}


def test_three_of_five_unstable_with_distribution():
    cases = aggregate_repetitions(
        [_r("a", "pass")] * 3 + [_r("a", "fail")] * 2 + [_r("b", "pass")] * 5
    )
    a, b = cases["a"], cases["b"]
    assert a["status"] == "unstable" and a["repetitions"] == 5
    assert a["distribution"] == {"pass": 3, "fail": 2, "unknown": 0}
    assert a["entropy"] == 0.971
    assert b["status"] == "pass" and b["entropy"] == 0.0
    assert binary_entropy(2, 2) == 1.0 and binary_entropy(0, 4) == 0.0


def test_unknown_repetitions_do_not_make_a_case_unstable():
    cases = aggregate_repetitions([_r("a", "pass"), _r("a", "unknown"), _r("b", "unknown")])
    assert cases["a"]["status"] == "pass" and cases["b"]["status"] == "unknown"


def test_unstable_not_regression_by_default():
    summary = {"pass": 3, "fail": 0, "unknown": 0, "unstable": 1, "cases": 4, "pass_rate": 1.0}
    verdict = evaluate({"summary": summary}, None, Thresholds())
    assert verdict.passed and any("unstable" in n for n in verdict.notes)
    assert not evaluate({"summary": summary}, None, Thresholds(fail_on_unstable=True)).passed
    assert evaluate({"summary": summary}, None, Thresholds(max_unstable=1)).passed
    assert not evaluate({"summary": summary}, None, Thresholds(max_unstable=0)).passed


async def _post_run(client, ds, results):
    r = await client.post(f"/v1/datasets/{ds}/runs", json={"results": results})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.anyio
async def test_a_run_with_repetitions_reports_the_unstable_case(client, db_session):
    from tests.test_dataset_runs import _dataset_with_case, _trace

    ds, item = await _dataset_with_case(client)
    good = [await _trace(db_session) for _ in range(3)]
    bad = [await _trace(db_session, degraded=True) for _ in range(2)]
    results = [{"item_id": item, "trace_id": t.id, "repetition": n}
               for n, t in enumerate(good + bad)]
    run = await _post_run(client, ds, results)
    s = run["summary"]
    assert (s["cases"], s["results"], s["unstable"], s["pass"], s["fail"]) == (1, 5, 1, 0, 0)
    assert run["cases"][0]["distribution"] == {"pass": 3, "fail": 2, "unknown": 0}
    assert {r["repetition"] for r in run["results"]} == {0, 1, 2, 3, 4}
    # the same repetition twice is refused
    dup = [{"item_id": item, "trace_id": good[0].id, "repetition": 1}] * 2
    assert (await client.post(f"/v1/datasets/{ds}/runs", json={"results": dup})).status_code == 422
    # a stable baseline against it: the change is "unstable", not a regression
    base = await _post_run(client, ds, [{"item_id": item, "trace_id": good[0].id}])
    diff = (await client.get(f"/v1/datasets/{ds}/compare?base={base['id']}&candidate={run['id']}")
            ).json()
    assert diff["summary"]["unstable"] == 1 and diff["summary"]["regressed"] == 0
    assert (await client.get(f"/v1/datasets/runs/{run['id']}")).json()["summary"]["unstable"] == 1


@pytest.mark.anyio
async def test_runs_without_repetition_unchanged(client, db_session):
    from tests.test_dataset_runs import _dataset_with_case, _trace

    ds, item = await _dataset_with_case(client)
    trace = await _trace(db_session)
    run = await _post_run(client, ds, [{"item_id": item, "trace_id": trace.id}])
    s = run["summary"]
    assert (s["pass"], s["fail"], s["unknown"], s["cases"], s["pass_rate"]) == (1, 0, 0, 1, 1.0)
    assert s["unstable"] == 0 and run["cases"] == [] and run["results"][0]["repetition"] == 0


def test_migration_adds_and_removes_the_repetition_column(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    db = tmp_path / "rep.sqlite"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    backend = Path(__file__).resolve().parents[1]

    def alembic(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend, env=env,
                       check=True, capture_output=True)

    def columns():
        with sqlite3.connect(db) as conn:
            return {r[1] for r in conn.execute("PRAGMA table_info(run_results)")}

    alembic("upgrade", "head")
    assert "repetition" in columns()
    alembic("downgrade", "20261106_configs")
    assert "repetition" not in columns()
    alembic("upgrade", "head")
    assert "repetition" in columns()
