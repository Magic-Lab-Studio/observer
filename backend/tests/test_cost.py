"""Cost completed from the shared price table (ManitOS plan 2026-11-02, B3)."""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from app import cost
from app.cost import classify, estimate

ROOT = Path(__file__).resolve().parents[2]
SDK_TABLE = ROOT / "sdk/python/src/llm_observatory/pricing.json"
TS_TABLE = ROOT / "sdk/typescript/src/pricing.json"

# Model names as ManitOS sent them to the live Observer (2026-11-02).
MANITOS_NAMES = {
    "groq · qwen/qwen3.8-27b": ("priced", "qwen/qwen3.8-27b"),
    "qwen3.8-27b-groq": ("priced", "qwen/qwen3.8-27b"),
    "groq · openai/gpt-oss-20b": ("priced", "openai/gpt-oss-20b"),
    "gpt-oss-20b-groq": ("priced", "openai/gpt-oss-20b"),
    "gemini · gemini-2.5-flash": ("priced", "gemini-2.5-flash"),
    "gemini · gemini-2.5-flash-lite": ("priced", "gemini-2.5-flash-lite"),
    "gemini-3.8-flash": ("priced", "gemini-3.8-flash"),
    "cloudflare · @cf/meta/llama-3.3-70b-instruct-fp8-fast": (
        "priced",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    ),
    "cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast": (
        "priced",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    ),
    "openrouter · nvidia/nemotron-3-super-120b-a12b:free": ("free", None),
    "cohere · command-a-03-2025": ("unpriced", None),
    "nvidia:google/gemma-4-31b-it": ("unpriced", None),
    "llamacpp · google_gemma-3-4b-it-Q4_K_M": ("local", None),
    "google_gemma-3-4b-it-Q4_K_M.gguf": ("local", None),
    "ollama · granite4:3b": ("local", None),
    "gemini-3.6-flash": ("unpriced", None),
}


def test_backend_table_is_the_sdk_table():
    if not SDK_TABLE.exists():
        pytest.skip("SDK not in this checkout")
    assert json.loads(cost.TABLE_PATH.read_text()) == json.loads(SDK_TABLE.read_text())
    assert json.loads(cost.TABLE_PATH.read_text()) == json.loads(TS_TABLE.read_text())


def test_every_price_names_an_official_source_and_a_day():
    """A6: no "unverified" price is left; retired or unlisted models are unpriced with a reason."""
    data = cost.table()
    for key, entry in data["models"].items():
        assert entry.get("checked"), key
        assert "unverified" not in entry.get("source", ""), key
    assert classify("o3")["status"] == "priced"
    assert estimate("o3", 1_000_000, 1_000_000, datetime(2026, 11, 5))[1] == 10.0
    for retired in ("claude-3-opus", "gpt-4", "gemini-1.5-flash", "gemini-2.0-flash"):
        info = classify(retired)
        assert info["status"] == "unpriced" and info["reason"] != "not in the price table"


@pytest.mark.parametrize(("name", "expected"), list(MANITOS_NAMES.items()))
def test_every_name_manitos_sends_is_classified(name, expected):
    info = classify(name)
    assert (info["status"], info["key"]) == expected
    if info["status"] == "unpriced":
        assert info["reason"]


def test_unpriced_models_say_why():
    assert "Cohere" in classify("command-a-03-2025")["reason"]
    assert classify("gemini-3.6-flash")["reason"] == "not in the price table"


def test_paid_price_free_tier_and_price_change(monkeypatch):
    monkeypatch.delenv("OBSERVATORY_FREE_TIER_PROVIDERS", raising=False)
    day = datetime(2026, 11, 2)
    assert estimate("groq · qwen/qwen3.8-27b", 1_000_000, 1_000_000, day) == (4.8, 4.8)
    monkeypatch.setenv("OBSERVATORY_FREE_TIER_PROVIDERS", "groq, google")
    assert estimate("groq · qwen/qwen3.8-27b", 1_000_000, 1_000_000, day) == (0.0, 4.8)
    assert estimate("gemini-3.8-flash", 1_000_000, 0, day) == (0.0, 0.75)
    assert estimate("gemini-3.8-flash", 1_000_000, 0, datetime(2027, 1, 1)) == (0.0, 1.5)
    assert estimate("cloudflare · @cf/meta/llama-3.3-70b-instruct-fp8-fast", 1_000_000, 0, day) == (
        0.293,
        0.293,
    )


def test_unknown_is_never_reported_as_free():
    assert estimate("cohere · command-a-03-2025", 1000, 1000, None) == (None, None)
    assert estimate("groq · qwen/qwen3.8-27b", None, None, None) == (None, None)
    assert estimate("llamacpp · gemma", 1000, 1000, None) == (0.0, 0.0)
    assert estimate("openrouter · nvidia/nemotron-3-super-120b-a12b:free", 10, 10, None) == (
        0.0,
        None,
    )


def _turn(model, *, tokens=(1000, 500), cost_usd=None):
    now = time.time()
    span = {
        "id": str(uuid.uuid4()),
        "name": "model.answered",
        "span_type": "llm",
        "start_time": now - 1,
        "end_time": now,
        "status": "ok",
        "tokens_input": tokens[0],
        "tokens_output": tokens[1],
        "attributes": {"model": model},
    }
    if cost_usd is not None:
        span["cost_usd"] = cost_usd
    return {
        "schema_version": "manitos.telemetry.v1",
        "idempotency_key": str(uuid.uuid4()),
        "project_id": "manitos",
        "environment": "test",
        "service_instance_id": "test",
        "session_id": "session_cost",
        "turn_id": "turn_1",
        "trace": {
            "id": str(uuid.uuid4()),
            "name": "manitos.turn",
            "start_time": now - 2,
            "end_time": now,
            "status": "ok",
        },
        "spans": [span],
    }


async def _spans(client, payload):
    response = await client.post("/v1/ingest/manitos/traces", json=payload)
    assert response.status_code == 200, response.text
    return (await client.get(f"/v1/traces/{payload['trace']['id']}/spans")).json()


@pytest.mark.anyio
async def test_ingest_fills_a_missing_cost_and_keeps_a_sent_one(client, monkeypatch):
    monkeypatch.setenv("OBSERVATORY_FREE_TIER_PROVIDERS", "groq")
    (filled,) = await _spans(client, _turn("groq · qwen/qwen3.8-27b"))
    assert filled["cost_usd"] == 0.0
    assert filled["attributes"]["cost.equivalent_usd"] == pytest.approx(0.0028)
    (kept,) = await _spans(client, _turn("groq · qwen/qwen3.8-27b", cost_usd=0.5))
    assert kept["cost_usd"] == 0.5
    assert "cost.equivalent_usd" not in kept["attributes"]
    (unknown,) = await _spans(client, _turn("cohere · command-a-03-2025"))
    assert unknown["cost_usd"] is None


@pytest.mark.anyio
async def test_a_resent_batch_still_matches_what_was_stored(client):
    payload = _turn("gemini-2.5-flash")
    assert (await client.post("/v1/ingest/manitos/traces", json=payload)).status_code == 200
    again = await client.post("/v1/ingest/manitos/traces", json=payload)
    assert again.status_code == 200
    assert again.json()["status"] in {"accepted", "duplicate"}


@pytest.mark.anyio
async def test_sdk_batch_spans_are_priced_too(client, monkeypatch):
    monkeypatch.delenv("OBSERVATORY_FREE_TIER_PROVIDERS", raising=False)
    now = time.time()
    body = {
        "spans": [
            {
                "name": "llm",
                "span_type": "llm",
                "start_time": now - 1,
                "end_time": now,
                "tokens_input": 1_000_000,
                "tokens_output": 0,
                "attributes": {"gen_ai.request.model": "gemini-2.5-flash-lite"},
            }
        ]
    }
    response = await client.post("/v1/traces/batch", json=body)
    assert response.status_code == 200, response.text
    trace_id = response.json()[0]["id"]
    (span,) = (await client.get(f"/v1/traces/{trace_id}/spans")).json()
    assert span["cost_usd"] == pytest.approx(0.10)


@pytest.mark.anyio
async def test_cost_by_model_shows_paid_equivalent_and_price_status(client, monkeypatch):
    monkeypatch.setenv("OBSERVATORY_FREE_TIER_PROVIDERS", "groq,google")
    await _spans(client, _turn("groq · qwen/qwen3.8-27b", tokens=(1_000_000, 0)))
    await _spans(client, _turn("gemini · gemini-2.5-flash", tokens=(0, 1_000_000)))
    await _spans(client, _turn("cohere · command-a-03-2025"))
    rows = {r["model"]: r for r in (await client.get("/v1/analytics/cost-by-model")).json()}
    groq = rows["groq · qwen/qwen3.8-27b"]
    assert groq["cost_usd"] == 0.0 and groq["equivalent_cost_usd"] == pytest.approx(0.8)
    assert groq["price_status"] == "priced" and groq["tokens_input"] == 1_000_000
    assert rows["gemini · gemini-2.5-flash"]["equivalent_cost_usd"] == pytest.approx(2.5)
    cohere = rows["cohere · command-a-03-2025"]
    assert cohere["price_status"] == "unpriced" and "Cohere" in cohere["price_note"]
