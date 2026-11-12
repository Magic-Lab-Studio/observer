"""Step 13a: cost per outcome. Buckets add up to the total, each costed span counted once, and the
waste and top-5% shares match a hand count on synthetic traces."""

import time
import uuid

import pytest

from app.cost_outcomes import BUCKETS, bucket_of, top_share


def _span(name, *, trace_id, parent=None, start=0.0, end=1.0, status="ok", **extra):
    now = time.time() - 60
    return {"id": extra.pop("id", None) or str(uuid.uuid4()), "trace_id": trace_id,
            "parent_id": parent, "name": name, "span_type": extra.pop("span_type", "generic"),
            "start_time": now + start, "end_time": now + end, "status": status, **extra}


def _llm(tid, root, cost, *, user=None, status="ok", finish="stop", start=0.1):
    attrs = {"gen_ai.request.model": "claude-sonnet-4-5",
             "gen_ai.response.finish_reasons": [finish] if finish else []}
    if user:
        attrs["user.id"] = user
    return _span("chat", trace_id=tid, parent=root, span_type="llm", start=start, end=start + 0.2,
                 status=status, cost_usd=cost, tokens_input=10, tokens_output=5, attributes=attrs)


async def _seed(client, project):
    spans, ids = [], {}

    def turn(key, children, *, status="ok"):
        tid, root = str(uuid.uuid4()), str(uuid.uuid4())
        ids[key] = tid
        spans.extend(c(tid, root) for c in children)
        spans.append(_span("agent", id=root, trace_id=tid, end=2.0, status=status,
                           attributes={"service.name": project}))

    turn("answered", [lambda t, r: _llm(t, r, 0.010, user="u1")])
    turn("failed", [lambda t, r: _llm(t, r, 0.004, user="u2", finish=None)], status="error")
    turn("errored", [lambda t, r: _llm(t, r, 0.002, user="u1", status="error", finish=None),
                     lambda t, r: _llm(t, r, 0.003, user="u1", start=0.5)])

    def tool(cost):
        return lambda t, r: _span("search", trace_id=t, parent=r, span_type="tool", cost_usd=cost,
                                  input={"q": "same"}, output={"hits": 1},
                                  attributes={"user.id": "u3"})
    # five identical calls: loop_detection's default threshold (OBSERVATORY_LOOP_MIN_REPEATS)
    turn("repeated", [tool(0.001) for _ in range(5)] + [
        lambda t, r: _llm(t, r, 0.0, user="u3", start=1.0)])
    turn("unknown", [lambda t, r: _llm(t, r, 0.005, finish=None)])
    r = await client.post("/v1/traces/batch", json={"spans": spans})
    assert r.status_code == 200, r.text
    return ids


@pytest.mark.anyio
async def test_buckets_sum_to_total_and_match_a_hand_count(client):
    await _seed(client, "c1")
    s = (await client.get("/v1/costs/summary?project_id=c1&hours=1")).json()
    b = s["buckets"]
    assert set(b) == set(BUCKETS)
    assert sum(b.values()) == pytest.approx(s["total_usd"]) == pytest.approx(0.029)
    assert b == pytest.approx({"useful": 0.014, "failed": 0.004, "error": 0.002,
                               "repeated": 0.004, "unknown": 0.005})
    assert s["waste_share"] == pytest.approx(round(0.010 / 0.029, 4))
    # all the spend over the three answered turns: failed work raises the cost per answer
    assert s["answered_turns"] == 3 and s["cost_per_answered"] == pytest.approx(0.029 / 3, abs=1e-6)


@pytest.mark.anyio
async def test_each_span_counted_once(client):
    await _seed(client, "c2")
    s = (await client.get("/v1/costs/summary?project_id=c2&hours=1")).json()
    assert s["costed_spans"] == 11  # 1 + 1 + 2 + 5 tools + 1 zero-cost llm + 1
    assert sum(m["spans"] for m in s["routing"]) == 6  # the costed llm spans name a model
    assert sum(m["cost_usd"] for m in s["routing"]) == pytest.approx(0.024)


@pytest.mark.anyio
async def test_top5_share_hand_count(client):
    await _seed(client, "c3")
    c = (await client.get("/v1/costs/summary?project_id=c3&hours=1")).json()["concentration"]
    # u1 0.015, u2 0.004, u3 0.005; 5% of 3 users is one user: 0.015 / 0.024
    assert (c["users"], c["top_users"]) == (3, 1)
    assert c["top5_share"] == pytest.approx(round(0.015 / 0.024, 4))
    assert c["unattributed_usd"] == pytest.approx(0.005)


def test_bucket_rules_and_top_share_are_pure():
    class S:
        def __init__(self, status="ok", id="x"):
            self.status, self.id = status, id

    assert bucket_of(S("error"), "answered", set()) == "error"
    assert bucket_of(S(id="r"), "answered", {"r"}) == "repeated"
    assert bucket_of(S(), "cancelled", set()) == "failed"
    assert bucket_of(S(), "degraded", set()) == "useful"
    assert bucket_of(S(), "unknown", set()) == "unknown"
    costs = {f"u{i}": 1.0 for i in range(39)} | {"heavy": 60.0}
    share, top = top_share(costs)
    assert top == 2 and share == pytest.approx(61 / 99)  # 5% of 40 users is two
    assert top_share({}) == (None, 0)


@pytest.mark.anyio
async def test_an_empty_window_has_no_shares(client):
    s = (await client.get("/v1/costs/summary?project_id=nobody&hours=1")).json()
    assert s["total_usd"] == 0 and s["waste_share"] is None and s["cost_per_answered"] is None


@pytest.mark.anyio
async def test_waste_share_alert_opens(client, db_session):
    from app.alerts import evaluate_rules
    from app.models.alerts import AlertRule

    await _seed(client, "c4")
    db_session.add(AlertRule(name="desperdicio", metric="waste_share", comparator=">=",
                             threshold=0.25, window_minutes=60, min_samples=3, project_id="c4"))
    await db_session.commit()
    assert (await evaluate_rules(db_session)).opened == 1


@pytest.mark.anyio
async def test_waste_share_alert_stays_closed_under_its_threshold(client, db_session):
    from app.alerts import evaluate_rules
    from app.models.alerts import AlertRule

    await _seed(client, "c5")
    db_session.add(AlertRule(name="desperdicio alto", metric="waste_share", comparator=">=",
                             threshold=0.5, window_minutes=60, min_samples=3, project_id="c5"))
    await db_session.commit()
    result = await evaluate_rules(db_session)
    assert result.evaluated >= 1 and result.opened == 0  # 0.345 is under 0.5


async def _owned_turn(client, project):
    """One answered turn: a paid call (0.010) and two local calls of 36 s each, with no cost."""
    tid, root = str(uuid.uuid4()), str(uuid.uuid4())
    now = time.time() - 600

    def local(start):
        return {"id": str(uuid.uuid4()), "trace_id": tid, "parent_id": root, "name": "chat",
                "span_type": "llm", "start_time": now + start, "end_time": now + start + 36,
                "status": "ok", "attributes": {"gen_ai.request.model": "llama3.2:3b",
                                               "gen_ai.system": "ollama",
                                               "gen_ai.response.finish_reasons": ["stop"]}}
    spans = [_llm(tid, root, 0.010, user="u1"), local(1), local(40),
             {"id": root, "trace_id": tid, "parent_id": None, "name": "agent",
              "span_type": "generic", "start_time": now, "end_time": now + 80, "status": "ok",
              "attributes": {"service.name": project}}]
    assert (await client.post("/v1/traces/batch", json={"spans": spans})).status_code == 200
    return tid


@pytest.mark.anyio
async def test_local_with_hourly_cost_not_free(client):
    tid = await _owned_turn(client, "own")
    before = (await client.get("/v1/costs/summary?project_id=own&hours=1")).json()
    assert before["total_usd"] == pytest.approx(0.010) and before["owned_usd"] == 0
    put = await client.put("/v1/projects/own/config/cost", json={"hourly_usd": {"local": 0.5}})
    assert put.status_code == 200, put.text
    s = (await client.get("/v1/costs/summary?project_id=own&hours=1")).json()
    # 72 s of local calls at 0.50 USD/h = 0.01
    assert s["owned_usd"] == pytest.approx(0.01) and s["paid_usd"] == pytest.approx(0.010)
    assert s["total_usd"] == pytest.approx(0.02) == pytest.approx(sum(s["buckets"].values()))
    local = next(m for m in s["routing"] if m["model"] == "llama3.2:3b")
    assert local["pricing"] == "local" and local["owned_usd"] == pytest.approx(0.01)
    # computed when read, never written to the spans
    spans = (await client.get(f"/v1/traces/{tid}/spans")).json()
    assert all(sp["cost_usd"] is None for sp in spans if sp["name"] == "chat"
               and (sp.get("attributes") or {}).get("gen_ai.system") == "ollama")


@pytest.mark.anyio
async def test_ingest_cost_unchanged_and_bad_rates_refused(client):
    await _owned_turn(client, "own2")
    assert (await client.put("/v1/projects/own2/config/cost",
                             json={"hourly_usd": {"local": -1}})).status_code == 422
    assert (await client.put("/v1/projects/own2/config/cost",
                             json={"hourly_usd": {"local": 1}, "x": 1})).status_code == 422
    s = (await client.get("/v1/costs/summary?project_id=own2&hours=1")).json()
    assert s["paid_usd"] == pytest.approx(0.010) and s["owned_usd"] == 0
