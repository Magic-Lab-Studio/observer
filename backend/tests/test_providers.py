"""Provider chain endpoint: snapshot storage and llm.chain aggregation."""

from datetime import datetime, timedelta, timezone

import pytest

from app.api.providers import aggregate, parse_attempts
from app.models import Span, Trace


def test_parse_attempts_reads_ms_models_with_colons_and_dashes():
    got = parse_attempts("groq:qwen/qwen3.8-27b=429@412ms>ollama:granite4:3b=ok@900ms>llamacpp:-=ok>junk")
    assert [(a["backend"], a["model"], a["result"], a["ms"]) for a in got] == [
        ("groq", "qwen/qwen3.8-27b", "429", 412),
        ("ollama", "granite4:3b", "ok", 900),
        ("llamacpp", "", "ok", None),
    ]


def test_aggregate_counts_depth_paths_and_percentiles():
    now = datetime.now(timezone.utc)
    rows = [
        ({"attempts_text": "groq:q=ok@1000ms", "answered_by": "groq · q"}, now, 1500.0),
        ({"attempts_text": "groq:q=429@300ms>cloudflare:llama=ok@2000ms", "answered_by": "cloudflare · llama"}, now, 2600.0),
        ({"attempts_text": "groq:q=skipped_exhausted>llamacpp:-=ok@8000ms", "answered_by": "llamacpp · gemma"}, now, 9000.0),
    ]
    out = aggregate(rows)
    by = {p["id"]: p for p in out["providers"]}
    assert out["turns"] == 3
    assert by["groq"]["attempts"] == 3 and by["groq"]["rate_limited"] == 1 and by["groq"]["skipped"] == 1
    assert by["groq"]["success_rate"] == pytest.approx(0.5)  # skipped calls are not attempts that could fail
    assert by["cloudflare"]["answered"] == 1 and by["cloudflare"]["p50_ms"] == 2000
    assert {d["depth"]: d["count"] for d in out["fallback_depth"]} == {0: 2, 1: 1}
    assert {p["path"] for p in out["paths"]} == {"groq", "groq → cloudflare", "groq → llamacpp"}


@pytest.mark.anyio
async def test_snapshot_roundtrip_and_stats_endpoint(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("OBSERVATORY_PROVIDERS_SNAPSHOT", str(tmp_path / "snap.json"))
    now = datetime.now(timezone.utc)
    trace = Trace(name="manitos.turn", start_time=now - timedelta(seconds=3), end_time=now, status="ok",
                  project_id="manitos", environment="test", created_at=now)
    db_session.add(trace)
    await db_session.flush()
    db_session.add(Span(trace_id=trace.id, name="llm.chain", span_type="event", start_time=now, status="ok",
                        attributes={"attempts_text": "groq:q=429>nvidia:gemma=ok@700ms", "answered_by": "nvidia · gemma"}))
    await db_session.commit()

    snap = {"generated_at": 1.0, "providers": [
        {"id": "nvidia", "label": "NVIDIA NIM", "kind": "cloud", "order": 3, "active": True, "has_key": True,
         "models": [{"model": "gemma", "label": "Gemma"}], "data_note": "free key", "out_of_quota": []}]}
    assert (await client.post("/v1/providers/snapshot", json=snap)).status_code == 200
    bad = {"generated_at": 1.0, "providers": [{"id": "Bad Id!"}]}
    assert (await client.post("/v1/providers/snapshot", json=bad)).status_code == 422

    data = (await client.get("/v1/providers/?hours=1")).json()
    assert data["snapshot"]["providers"][0]["id"] == "nvidia" and data["snapshot"]["stale"] is False
    assert data["turns"] == 1 and data["providers"][0]["id"] in {"groq", "nvidia"}
    assert any(p["id"] == "nvidia" and p["answered"] == 1 for p in data["providers"])
