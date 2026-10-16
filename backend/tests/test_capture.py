import json
import uuid

import pytest
from sqlalchemy import select

from app.capture import (
    apply_capture,
    content_like_keys,
    filter_attributes,
    has_sensitive_patterns,
    redact_json,
    redact_text,
)
from app.models import Span


def test_redaction_masks_known_patterns_and_leaves_ordinary_text():
    text = (
        "Mail ana.p@example.com, phone +57 300 123 4567 or 415-555-2671, card 4111 1111 1111 1111, "
        "ssn 123-45-6789, header Bearer abcdef123456789, key sk-live1234567890abcdef, "
        "jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY3.SflKxwRJSMeKKF2QT4fw"
    )
    out = redact_text(text)
    for secret in (
        "ana.p@",
        "300 123",
        "415-555",
        "4111",
        "123-45",
        "abcdef123456789",
        "sk-live",
        "eyJhbGci",
    ):
        assert secret not in out
    for label in ("email", "phone", "card", "ssn", "bearer", "key", "jwt"):
        assert f"[REDACTED:{label}]" in out
    plain = "The meeting is on 2026-10-16 at room 12, order 20261016, version 1.45.1."
    assert redact_text(plain) == plain


def test_a_digit_run_that_is_not_a_card_is_kept():
    assert redact_text("order 4111 1111 1111 1112 shipped") == "order 4111 1111 1111 1112 shipped"


def test_secret_looking_keys_are_masked_whatever_the_value():
    data = {
        "user": "x",
        "api_key": "plain",
        "nested": {"Authorization": "abc", "ok": 1},
        "list": [{"password": "hunter2"}],
    }
    out = redact_json(data)
    assert (
        out["api_key"] == "[REDACTED:secret]"
        and out["nested"]["Authorization"] == "[REDACTED:secret]"
    )
    assert out["list"][0]["password"] == "[REDACTED:secret]" and out["nested"]["ok"] == 1


def test_modes_decide_what_is_kept():
    kw = {
        "inp": {"q": "mail me@x.io"},
        "out": {"a": "hi"},
        "attributes": {"model": "m", "gen_ai.prompt": "P", "gen_ai.usage.input_tokens": 5},
        "meta": {"note": "me@x.io"},
    }
    inp, out, attrs, meta = apply_capture("metadata", **kw)
    assert inp is None and out is None and attrs == {"model": "m", "gen_ai.usage.input_tokens": 5}
    assert meta == kw["meta"]
    inp, out, attrs, meta = apply_capture("redacted", **kw)
    assert inp == {"q": "mail [REDACTED:email]"} and out == {"a": "hi"} and "gen_ai.prompt" in attrs
    assert meta == {"note": "[REDACTED:email]"}
    assert apply_capture("full", **kw) == (kw["inp"], kw["out"], kw["attributes"], kw["meta"])


def test_report_helpers():
    assert content_like_keys(
        {"model": "m", "gen_ai.prompt": "x", "gen_ai.usage.input_tokens": 1}
    ) == ["gen_ai.prompt"]
    assert has_sensitive_patterns({"a": "me@x.io"}) and not has_sensitive_patterns(
        {"a": "hi"}, None
    )
    assert filter_attributes(None, "metadata") is None
    long = filter_attributes({"model": "x" * 500}, "metadata")
    assert len(long["model"]) == 200


def _manitos_payload(project, *, key=None):
    tid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    return {
        "schema_version": "manitos.telemetry.v1",
        "idempotency_key": key or f"k-{uuid.uuid4()}",
        "project_id": project,
        "environment": "test",
        "service_instance_id": "i",
        "session_id": "s",
        "turn_id": "t",
        "trace": {
            "id": tid,
            "name": "turn",
            "start_time": 1_750_000_000.0,
            "end_time": 1_750_000_001.0,
            "status": "ok",
            "metadata": {"who": "me@x.io"},
        },
        "spans": [
            {
                "id": sid,
                "name": "llm",
                "span_type": "llm",
                "start_time": 1_750_000_000.1,
                "end_time": 1_750_000_000.9,
                "status": "ok",
                "input": {"text": "write to ana@example.com"},
                "output": {"text": "done"},
                "attributes": {"model": "m", "message_id": "abc"},
            }
        ],
    }


async def _stored_span(db, trace_id):
    return (await db.execute(select(Span).where(Span.trace_id == trace_id))).scalars().one()


async def _ingest(client, db_session, project, mode):
    if mode:
        res = await client.put(f"/v1/privacy/projects/{project}", json={"capture_mode": mode})
        assert res.status_code == 200
    payload = _manitos_payload(project)
    assert (await client.post("/v1/ingest/manitos/traces", json=payload)).status_code == 200
    db_session.expire_all()
    return await _stored_span(db_session, payload["trace"]["id"])


@pytest.mark.anyio
async def test_manitos_path_default_stores_what_is_sent_and_modes_override(client, db_session):
    span = await _ingest(client, db_session, "p-default", None)
    assert span.inp == {"text": "write to ana@example.com"}  # historical behaviour kept
    span = await _ingest(client, db_session, "p-meta", "metadata")
    assert span.inp is None and span.out is None and span.attributes == {"model": "m"}
    span = await _ingest(client, db_session, "p-red", "redacted")
    assert span.inp == {"text": "write to [REDACTED:email]"} and span.out == {"text": "done"}
    span = await _ingest(client, db_session, "p-full", "full")
    assert span.inp == {"text": "write to ana@example.com"}


@pytest.mark.anyio
async def test_env_default_applies_to_paths_without_a_project_mode(client, db_session, monkeypatch):
    monkeypatch.setenv("OBSERVATORY_CAPTURE_DEFAULT", "redacted")
    span = await _ingest(client, db_session, "p-env", None)
    assert span.inp == {"text": "write to [REDACTED:email]"}
    span = await _ingest(client, db_session, "p-env-override", "full")  # a project mode wins
    assert span.inp == {"text": "write to ana@example.com"}


@pytest.mark.anyio
async def test_sdk_path_follows_the_project_mode(client, db_session):
    await client.put("/v1/privacy/projects/sdk-proj", json={"capture_mode": "metadata"})
    body = {
        "name": "t",
        "start_time": 1_750_000_000.0,
        "status": "ok",
        "spans": [
            {
                "name": "s",
                "span_type": "llm",
                "start_time": 1_750_000_000.0,
                "status": "ok",
                "input": {"q": "secret"},
                "output": {"a": "x"},
            }
        ],
    }
    res = await client.post("/v1/traces/", json=body)
    assert res.status_code == 200
    # Traces posted without a project use the path default (stored as sent).
    span = await _stored_span(db_session, res.json()["id"])
    assert span.inp == {"q": "secret"}
    imported = {
        "traces": [
            {
                "name": "t",
                "start_time": 1_750_000_000.0,
                "status": "ok",
                "project_id": "sdk-proj",
                "spans": body["spans"],
            }
        ]
    }
    res = await client.post("/v1/traces/import", json=imported)
    assert res.status_code == 200
    db_session.expire_all()
    span = await _stored_span(db_session, res.json()["trace_ids"][0])
    assert span.inp is None and span.out is None


@pytest.mark.anyio
async def test_otlp_project_mode_overrides_the_env_default(client, db_session):
    def request(project, at):
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": project}}]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": uuid.uuid4().hex,
                                    "spanId": "aaaabbbbccccdddd",
                                    "name": "chat",
                                    "startTimeUnixNano": str(1760000000000000000 + at * 10**9),
                                    "endTimeUnixNano": str(1760000001000000000 + at * 10**9),
                                    "attributes": [
                                        {
                                            "key": "gen_ai.prompt",
                                            "value": {"stringValue": "ana@example.com"},
                                        },
                                        {
                                            "key": "gen_ai.request.model",
                                            "value": {"stringValue": "m"},
                                        },
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }

    clock = iter(range(1, 100))

    async def send(project):
        res = await client.post(
            "/v1/otlp/v1/traces",
            content=json.dumps(request(project, next(clock))),
            headers={"content-type": "application/json"},
        )
        assert res.status_code == 200
        db_session.expire_all()
        spans = (await db_session.execute(select(Span).order_by(Span.start_time.desc()))).scalars()
        return next(s for s in spans if s.name == "chat").attributes

    assert "gen_ai.prompt" not in await send("otlp-default")  # OTLP default: metadata
    await client.put("/v1/privacy/projects/otlp-full", json={"capture_mode": "full"})
    assert (await send("otlp-full"))["gen_ai.prompt"] == "ana@example.com"
    await client.put("/v1/privacy/projects/otlp-red", json={"capture_mode": "redacted"})
    assert (await send("otlp-red"))["gen_ai.prompt"] == "[REDACTED:email]"


@pytest.mark.anyio
async def test_report_and_enforce_rewrite_what_is_stored(client, db_session):
    await _ingest(client, db_session, "audit", "full")
    report = (await client.get("/v1/privacy/projects")).json()
    audit = next(p for p in report["projects"] if p["project_id"] == "audit")
    assert audit["source"] == "setting" and audit["capture_mode"] == "full"
    assert audit["report"]["with_input"] == 1 and audit["report"]["with_sensitive_patterns"] == 1
    # Enforcing needs a mode and an explicit confirmation: it rewrites stored data.
    assert (
        await client.post("/v1/privacy/projects/nope/enforce", json={"confirm": True})
    ).status_code == 409
    await client.put("/v1/privacy/projects/audit", json={"capture_mode": "metadata"})
    assert (await client.post("/v1/privacy/projects/audit/enforce", json={})).status_code == 422
    done = (await client.post("/v1/privacy/projects/audit/enforce", json={"confirm": True})).json()
    assert done["spans_scanned"] == 1 and done["spans_changed"] == 1
    after = next(
        p
        for p in (await client.get("/v1/privacy/projects")).json()["projects"]
        if p["project_id"] == "audit"
    )["report"]
    assert after["with_input"] == 0 and after["with_output"] == 0
    assert (await client.delete("/v1/privacy/projects/audit")).json()["source"] == "default"
    assert (
        await client.put("/v1/privacy/projects/x", json={"capture_mode": "bogus"})
    ).status_code == 422
