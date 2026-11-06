"""B5: the Overview's histogram, slowest list and stage card come from every trace in range."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.trace import Trace
from app.overview import histogram, stage_label, summarize


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_histogram_uses_the_dashboards_edges():
    rows = histogram([10, 999, 1000, 4999, 25_000, 40_000, 90_000])
    assert [r["count"] for r in rows] == [2, 1, 1, 0, 0, 1, 2]
    assert [r["from"] for r in rows] == [0, 1000, 2000, 5000, 10000, 20000, 40000]


def test_stage_labels_merge_text_and_audio():
    assert stage_label("text.llm") == stage_label("audio.llm") == "llm"


def test_summarize_places_by_start_and_ranks_the_slowest():
    end = _now()
    start = end - timedelta(hours=1)
    rows = [
        (f"t{i}", f"job {i}", None, "error" if i == 0 else "ok",
         start + timedelta(minutes=i), start + timedelta(minutes=i, seconds=i + 1),
         {"stage_timings_ms": {"text.llm": 100.0 * (i + 1), "tts": 50}})
        for i in range(10)
    ] + [("open", "running", None, "unset", start + timedelta(minutes=30), None, None)]
    out = summarize(rows, start=start, end=end, buckets=6, truncated=False)
    assert out["total"] == 11 and out["errors"] == 1 and out["durations"]["count"] == 10
    assert [b["total"] for b in out["buckets"]] == [10, 0, 0, 1, 0, 0]
    assert out["buckets"][0]["err"] == 1
    assert [s["id"] for s in out["slowest"]] == ["t9", "t8", "t7", "t6", "t5", "t4"]
    assert out["durations"]["max"] == 10_000.0 and out["durations"]["p50"] == 5000.0
    assert out["stages"][0] == {"stage": "llm", "avg": 550.0, "p95": 1000.0, "n": 10}


def _trace(i: int, *, project: str, env: str, minutes_ago: float, seconds: float) -> Trace:
    started = _now() - timedelta(minutes=minutes_ago)
    return Trace(name=f"run {i}", start_time=started, end_time=started + timedelta(seconds=seconds),
                 status="ok", project_id=project, environment=env, created_at=started,
                 meta={"stage_timings_ms": {"retrieve": 200}})


@pytest.mark.anyio
async def test_the_route_filters_by_window_project_and_environment(client, db_session):
    db_session.add_all([
        _trace(1, project="a", env="prod", minutes_ago=10, seconds=3),
        _trace(2, project="a", env="dev", minutes_ago=20, seconds=12),
        _trace(3, project="b", env="prod", minutes_ago=30, seconds=50),
        _trace(4, project="a", env="prod", minutes_ago=60 * 30, seconds=99),  # outside 24 h
    ])
    await db_session.commit()
    every = (await client.get("/v1/analytics/overview?hours=24")).json()
    assert every["total"] == 3 and every["slowest"][0]["name"] == "run 3"
    assert [h["count"] for h in every["histogram"]] == [0, 0, 1, 0, 1, 0, 1]
    assert every["stages"] == [{"stage": "retrieve", "avg": 200.0, "p95": 200.0, "n": 3}]
    only = (await client.get("/v1/analytics/overview?hours=24&project_id=a&environment=prod")
            ).json()
    assert only["total"] == 1 and only["slowest"][0]["name"] == "run 1"
    envs = (await client.get("/v1/analytics/environments")).json()
    assert envs == ["dev", "prod"]
    assert (await client.get("/v1/analytics/environments?project_id=b")).json() == ["prod"]
