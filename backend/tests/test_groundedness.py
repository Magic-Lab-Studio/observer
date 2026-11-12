"""Step 9: groundedness in ``rules`` mode (no model), configured per project."""

import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import groundedness
from app.alerts import evaluate_rules
from app.auto_eval import evaluate_groundedness
from app.groundedness import score_reply, split_claims
from app.models.alerts import AlertRule

BACKEND = Path(__file__).resolve().parents[1]
TOOL_OUT = ("Order 4471 shipped on 3 March. The refund for order 4471 is 120 USD and takes 14 "
            "days. Source: https://shop.example/orders/4471")
INVENTED = ("Your order 4471 shipped on 3 March. The refund is 180 USD and takes 14 days. "
            "Is there anything else?")
TWIN = ("Your order 4471 shipped on 3 March. The refund is 120 USD and takes 14 days. "
        "Is there anything else?")


def test_an_invented_number_makes_the_reply_ungrounded_and_is_listed():
    bad = score_reply(INVENTED, TOOL_OUT, fetched=set(), sources=1)
    assert bad["status"] == "ungrounded" and bad["reason"] == "invented_number"
    assert bad["claims"] == 2 and bad["supported"] == 1
    assert bad["unsupported"] == [{"claim": "The refund is 180 USD and takes 14 days.",
                                   "why": "invented_number", "detail": "180"}]
    good = score_reply(TWIN, TOOL_OUT, fetched=set(), sources=1)
    assert good["status"] == "grounded" and good["score"] == 1.0 and good["unsupported"] == []


def test_citations_must_point_to_what_the_trace_fetched():
    fetched = {"https://shop.example/orders/4471"}
    ok = score_reply("Refunds take a while for everyone [details](https://shop.example/orders/4471).",
                     TOOL_OUT, fetched=fetched, sources=1)
    assert ok["status"] == "grounded" and ok["cited"] == 1
    bare = score_reply("Refunds take a while for everyone, see https://shop.example/orders/4471.",
                       TOOL_OUT, fetched=fetched, sources=1)
    assert bare["status"] == "grounded"  # the sentence's full stop is not part of the URL
    bad = score_reply("Refunds take a while for everyone, see https://elsewhere.example/policy.",
                      TOOL_OUT, fetched=fetched, sources=1)
    assert bad["status"] == "ungrounded"
    assert bad["unsupported"][0]["why"] == "unfetched_source"
    assert bad["unsupported"][0]["detail"] == "https://elsewhere.example/policy"


def test_a_decimal_number_from_a_tool_matches_the_reply_however_it_is_written():
    # a JSON float in a tool's output (1492650.0) used to lose its decimal point and gain a digit
    ctx = str({"resultado": 1492650.0, "tasa": 4012.5, "total": 372.0})
    for reply in ("Serían 1.492.650 COP al mes en total.", "Serían 1,492,650 COP al mes en total.",
                  "Serían 1492650 COP al mes en total.", "La tasa usada es de 4.012,5 pesos hoy.",
                  "La tasa usada es de 4,012.5 pesos hoy.", "El total mensual es de 372 USD."):
        got = score_reply(reply, ctx, fetched=set(), sources=1)
        assert got["status"] == "grounded", (reply, got["unsupported"])
    bad = score_reply("Serían 1.500.000 COP al mes en total.", ctx, fetched=set(), sources=1)
    assert bad["reason"] == "invented_number" and bad["unsupported"][0]["detail"] == "1.500.000"
    # a decimal is not a thousands group: 3.72 is not 372
    assert score_reply("El total mensual es de 3.72 USD.", ctx, fetched=set(), sources=1)[
        "reason"] == "invented_number"


def test_a_number_the_user_gave_is_not_invented():
    ctx = "Plan Empresa: 31 USD por usuario al mes."
    reply = "Para 12 personas con el plan Empresa a 31 USD cada una, son 372 USD al mes."
    assert score_reply(reply, ctx + " total 372", fetched=set(), sources=1)["reason"] == (
        "invented_number")  # 12 is in no evidence
    got = score_reply(reply, ctx + " total 372", fetched=set(), sources=1,
                      given="Somos 12 personas, ¿cuánto pagaríamos?")
    assert got["status"] == "grounded", got["unsupported"]
    # the user's numbers count for numbers only: they do not make a sentence supported by words
    assert score_reply("Nuestros robots del almacén son de color verde.", ctx, fetched=set(),
                       sources=1, given="robots almacén color verde")["status"] != "grounded"


def test_unsupported_wording_is_partial_or_ungrounded_by_threshold():
    reply = ("Order 4471 shipped on 3 March. The refund for order 4471 is 120 USD. "
             "Our warehouse robots are painted green.")
    partial = score_reply(reply, TOOL_OUT, fetched=set(), sources=1, threshold=0.8)
    assert partial["status"] == "partial" and partial["score"] == pytest.approx(2 / 3, abs=1e-4)
    assert score_reply(reply, TOOL_OUT, fetched=set(), sources=1, threshold=0.6)["status"] == (
        "grounded")


def test_questions_short_lines_and_code_are_not_claims():
    assert split_claims("Hi! Can I help you with that order?\n```\nprice = 999\n```\nOk.") == []
    assert score_reply("Hello there!", TOOL_OUT, fetched=set(), sources=1)["status"] == (
        "not_applicable")


def _spans(project: str, key: str, reply: str, tool_out: str | None) -> list[dict]:
    tid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project}:{key}"))
    root = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project}:{key}:root"))
    t0 = time.time() - 300
    spans = [{"id": root, "trace_id": tid, "parent_id": None, "name": "support.turn",
              "span_type": "agent", "start_time": t0, "end_time": t0 + 2, "status": "ok",
              "input": {"user": "where is my refund for 4471?"}, "output": {"reply": reply},
              "attributes": {"service.name": project, "observer.task": "support"}},
             {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{tid}:llm")), "trace_id": tid,
              "parent_id": root, "name": "chat", "span_type": "llm", "start_time": t0 + 1,
              "end_time": t0 + 1.9, "status": "ok",
              "attributes": {"gen_ai.request.model": "llama3.2:3b",
                             "gen_ai.provider.name": "ollama",
                             "gen_ai.response.finish_reasons": ["stop"]}}]
    if tool_out is not None:
        spans.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{tid}:tool")), "trace_id": tid,
                      "parent_id": root, "name": "orders.lookup", "span_type": "tool",
                      "start_time": t0 + 0.1, "end_time": t0 + 0.9, "status": "ok",
                      "input": {"url": "https://shop.example/orders/4471"},
                      "output": {"body": tool_out}, "attributes": {"tool.name": "orders.lookup"}})
    return spans


async def _seed(client, project: str) -> dict[str, str]:
    spans = (_spans(project, "invented", INVENTED, TOOL_OUT)
             + _spans(project, "twin", TWIN, TOOL_OUT)
             + _spans(project, "no_context", TWIN, None))
    r = await client.post("/v1/traces/batch", json={"spans": spans})
    assert r.status_code == 200, r.text
    return {k: str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project}:{k}"))
            for k in ("invented", "twin", "no_context")}


@pytest.mark.anyio
async def test_the_config_is_closed_off_by_default_and_judge_is_not_offered_yet(client):
    base = "/v1/projects/g1/config"
    first = (await client.get(f"{base}/groundedness")).json()
    assert first["source"] == "default" and first["value"]["mode"] == "off"
    assert (await client.put(f"{base}/groundedness", json={"mode": "rules", "x": 1})
            ).status_code == 422
    assert (await client.put(f"{base}/groundedness", json={"mode": "judge"})).status_code == 422
    assert (await client.put(f"{base}/costs", json={})).status_code == 404
    saved = (await client.put(f"{base}/groundedness",
                              json={"mode": "rules", "threshold": 0.7,
                                    "context": {"span_types": ["tool"]}})).json()
    assert saved["source"] == "setting" and saved["value"]["threshold"] == 0.7
    every = (await client.get(base)).json()["sections"]["groundedness"]
    assert every["value"]["context"]["span_types"] == ["tool"]
    assert (await client.delete(f"{base}/groundedness")).json()["value"]["mode"] == "off"
    assert (await client.get(f"{base}/groundedness")).json()["source"] == "default"


@pytest.mark.anyio
async def test_mode_off_evaluates_nothing(client, db_session):
    ids = await _seed(client, "g-off")
    assert (await client.post("/v1/projects/g-off/groundedness/evaluate", json={})
            ).status_code == 409
    assert await evaluate_groundedness(db_session) == 0
    by_model = (await client.get("/v1/evaluations/by-model?project_id=g-off")).json()
    assert by_model["groundedness"]["count"] == 0
    assert ids


@pytest.mark.anyio
async def test_rules_mode_scores_the_planted_traces(client, db_session):
    ids = await _seed(client, "g-on")
    await client.put("/v1/projects/g-on/config/groundedness", json={"mode": "rules"})
    out = (await client.post("/v1/projects/g-on/groundedness/evaluate",
                             json={"trace_ids": list(ids.values())})).json()
    got = {r["trace_id"]: r for r in out["results"]}
    assert got[ids["invented"]]["status"] == "ungrounded"
    assert got[ids["invented"]]["unsupported"][0]["detail"] == "180"
    assert got[ids["twin"]]["status"] == "grounded" and got[ids["twin"]]["score"] == 1.0
    assert got[ids["twin"]]["model"] == "llama3.2:3b"
    assert got[ids["no_context"]]["status"] == "unknown"
    assert got[ids["no_context"]]["reason"] == "no_context"
    # the sweep finds nothing left to do; turn-health scores are not mixed with these
    assert await evaluate_groundedness(db_session) == 0
    by_model = (await client.get("/v1/evaluations/by-model?project_id=g-on")).json()
    g = by_model["groundedness"]
    assert (g["count"], g["scored"], g["unknown"], g["ungrounded_rate"]) == (3, 2, 1, 0.5)
    assert all(m["key"] != "?" or m["count"] == 0 for m in by_model["models"])
    query = {"view": "groundedness", "project_id": "g-on", "hours": 2,
             "metrics": [{"measure": "ungrounded", "aggregation": "avg"}],
             "dimensions": ["status"]}
    rows = (await client.post("/v1/analytics/query", json=query)).json()["rows"]
    assert {r["status"]: r["avg_ungrounded"] for r in rows} == {
        "grounded": 0.0, "ungrounded": 1.0, "unknown": None}


@pytest.mark.anyio
async def test_metadata_capture_keeps_no_context_so_it_stays_unknown(client):
    await client.put("/v1/privacy/projects/g-meta", json={"capture_mode": "metadata"})
    ids = await _seed(client, "g-meta")
    await client.put("/v1/projects/g-meta/config/groundedness", json={"mode": "rules"})
    out = (await client.post("/v1/projects/g-meta/groundedness/evaluate",
                             json={"trace_ids": [ids["invented"]]})).json()
    assert out["results"][0]["status"] == "unknown"
    assert out["results"][0]["reason"] in {"no_reply_text", "no_context"}


@pytest.mark.anyio
async def test_applies_to_leaves_other_tasks_not_applicable(client):
    ids = await _seed(client, "g-scope")
    await client.put("/v1/projects/g-scope/config/groundedness",
                     json={"mode": "rules", "applies_to": ["billing"]})
    out = (await client.post("/v1/projects/g-scope/groundedness/evaluate",
                             json={"trace_ids": [ids["invented"]]})).json()
    assert out["results"][0]["status"] == "not_applicable"
    await client.put("/v1/projects/g-scope/config/groundedness",
                     json={"mode": "rules", "applies_to": ["support"]})
    again = (await client.post("/v1/projects/g-scope/groundedness/evaluate",
                               json={"trace_ids": [ids["invented"]], "force": True})).json()
    assert again["results"][0]["status"] == "ungrounded"


@pytest.mark.anyio
async def test_the_ungrounded_rate_alert_opens(client, db_session):
    ids = await _seed(client, "g-alert")
    await client.put("/v1/projects/g-alert/config/groundedness", json={"mode": "rules"})
    await client.post("/v1/projects/g-alert/groundedness/evaluate",
                      json={"trace_ids": list(ids.values())})
    db_session.add(AlertRule(name="sin sustento", metric="ungrounded_rate", comparator=">=",
                             threshold=0.5, window_minutes=60, min_samples=2,
                             project_id="g-alert"))
    await db_session.commit()
    assert (await evaluate_rules(db_session)).opened == 1
    rate, samples = await groundedness.ungrounded_rate(
        db_session, project_id="g-alert", environment=None,
        since=datetime.now(timezone.utc) - timedelta(hours=1))
    assert (rate, samples) == (0.5, 2)


@pytest.mark.anyio
async def test_a_case_can_expect_groundedness(client):
    ids = await _seed(client, "g-case")
    ds = (await client.post("/v1/datasets", json={"name": "refunds", "project_id": "g-case"})
          ).json()
    expect = {"groundedness": {"min_level": "grounded"}}
    item = (await client.post(f"/v1/datasets/{ds['id']}/items",
                              json={"trace_id": ids["twin"], "expect": expect})).json()
    assert "groundedness" in item["checks"]

    async def run(trace: str) -> dict:
        body = {"results": [{"item_id": item["id"], "trace_id": trace}]}
        r = await client.post(f"/v1/datasets/{ds['id']}/runs", json=body)
        assert r.status_code == 200, r.text
        return r.json()["results"][0]

    assert (await run(ids["twin"]))["status"] == "unknown"  # off for this project
    await client.put("/v1/projects/g-case/config/groundedness", json={"mode": "rules"})
    assert (await run(ids["twin"]))["status"] == "pass"
    failed = await run(ids["invented"])
    assert failed["status"] == "fail" and "ungrounded" in failed["checks"][0]["detail"]


def test_migration_adds_and_removes_project_configs(tmp_path):
    db = tmp_path / "cfg.sqlite"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}

    def alembic(*args):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                       check=True, capture_output=True)

    def tables():
        with sqlite3.connect(db) as conn:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    alembic("upgrade", "head")
    assert "project_configs" in tables()
    alembic("downgrade", "20261102_span_type")  # the revision before project_configs
    assert "project_configs" not in tables()
    alembic("upgrade", "head")
    assert "project_configs" in tables()
