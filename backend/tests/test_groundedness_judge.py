"""Step 9: groundedness with a model judge (``judge``, ``rules+judge``), through a fake transport.

The judge sees the same bounded context as the rules. It never makes a pass out of a failure, sends
nothing when the mode is off or the capture kept no content, and a judge off this machine needs the
project's ``allow_remote``."""

import json
import time
import uuid

import pytest

from app import groundedness
from app.auto_eval import evaluate_groundedness
from app.evaluators.judge_backends import JudgeUnavailableError

# The planted twins of test_groundedness.py (copied: test modules here are not a package).
TOOL_OUT = ("Order 4471 shipped on 3 March. The refund for order 4471 is 120 USD and takes 14 "
            "days. Source: https://shop.example/orders/4471")
INVENTED = ("Your order 4471 shipped on 3 March. The refund is 180 USD and takes 14 days. "
            "Is there anything else?")
TWIN = ("Your order 4471 shipped on 3 March. The refund is 120 USD and takes 14 days. "
        "Is there anything else?")


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


LOCAL = {"provider": "openai-compatible", "model": "llama3.2:3b",
         "base_url": "http://127.0.0.1:11434/v1"}


class FakeJudge:
    """Flags the 180 USD claim (the context says 120); counts calls and keeps what it was sent."""

    def __init__(self, answer=None, error=None):
        self.calls = []
        self.answer = answer
        self.error = error

    async def __call__(self, config, system, user):
        self.calls.append({"config": config, "user": user})
        if self.error:
            raise self.error
        if self.answer is not None:
            return self.answer
        reply = user.split("REPLY TO CHECK:\n", 1)[1]
        if "180 USD" in reply:
            return json.dumps({"claims": 3, "level": "ungrounded", "unsupported": [
                {"claim": "The refund is 180 USD", "why": "contradicted"}]})
        return json.dumps({"claims": 3, "level": "grounded", "unsupported": []})


@pytest.fixture
def judge(monkeypatch):
    fake = FakeJudge()
    monkeypatch.setattr(groundedness, "ask_judge", fake)
    return fake


async def _evaluate(client, project, config, trace_ids):
    r = await client.put(f"/v1/projects/{project}/config/groundedness", json=config)
    assert r.status_code == 200, r.text
    out = (await client.post(f"/v1/projects/{project}/groundedness/evaluate",
                             json={"trace_ids": trace_ids, "force": True})).json()
    return {row["trace_id"]: row for row in out["results"]}


@pytest.mark.anyio
async def test_judge_mode_scores_the_planted_twins(client, judge):
    ids = await _seed(client, "j-on")
    got = await _evaluate(client, "j-on", {"mode": "judge", "judge": LOCAL},
                          [ids["invented"], ids["twin"]])
    invented, twin = got[ids["invented"]], got[ids["twin"]]
    assert invented["status"] == "ungrounded" and invented["mode"] == "judge"
    assert invented["unsupported"][0]["claim"] == "The refund is 180 USD"
    assert invented["judge_model"] == "llama3.2:3b"
    assert twin["status"] == "grounded" and twin["score"] == 1.0
    # the judge got the tool's output as context and the reply to check, nothing else
    sent = judge.calls[0]["user"]
    assert "refund for order 4471 is 120 USD" in sent and (INVENTED in sent or TWIN in sent)


@pytest.mark.anyio
async def test_judge_failure_is_unknown(client, monkeypatch):
    ids = await _seed(client, "j-fail")
    for fake, reason in ((FakeJudge(error=JudgeUnavailableError("judge_timeout", retryable=True)),
                          "judge_unavailable:judge_timeout"),
                         (FakeJudge(answer="looks fine to me"), "judge_invalid"),
                         (FakeJudge(answer='{"level": "excellent"}'), "judge_invalid")):
        monkeypatch.setattr(groundedness, "ask_judge", fake)
        got = await _evaluate(client, "j-fail", {"mode": "judge", "judge": LOCAL}, [ids["twin"]])
        assert got[ids["twin"]]["status"] == "unknown"
        assert got[ids["twin"]]["reason"] == reason and got[ids["twin"]]["score"] is None


@pytest.mark.anyio
async def test_rules_plus_judge_takes_worse(client, monkeypatch):
    ids = await _seed(client, "j-both")
    # the rules pass the twin; a judge that calls it partial makes it partial
    monkeypatch.setattr(groundedness, "ask_judge", FakeJudge(answer=json.dumps(
        {"claims": 2, "level": "partial", "unsupported": [{"claim": "takes 14 days"}]})))
    got = await _evaluate(client, "j-both", {"mode": "rules+judge", "judge": LOCAL}, [ids["twin"]])
    row = got[ids["twin"]]
    assert row["status"] == "partial" and row["score"] == 0.5
    assert row["rules_result"]["status"] == "grounded"
    assert row["judge_result"]["status"] == "partial"
    # a judge that fails leaves the combined result unknown, not the rules' pass
    monkeypatch.setattr(groundedness, "ask_judge", FakeJudge(answer="nope"))
    got = await _evaluate(client, "j-both", {"mode": "rules+judge", "judge": LOCAL}, [ids["twin"]])
    assert got[ids["twin"]]["status"] == "unknown"


def test_sampling_deterministic():
    ids = [f"trace-{n}" for n in range(400)]
    first = [groundedness.sampled(t, 0.25) for t in ids]
    assert first == [groundedness.sampled(t, 0.25) for t in ids]
    assert 60 < sum(first) < 140
    assert all(groundedness.sampled(t, 1.0) for t in ids)
    assert not any(groundedness.sampled(t, 0.0) for t in ids)


@pytest.mark.anyio
async def test_mode_off_sends_nothing(client, db_session, judge):
    await _seed(client, "j-off")
    await client.put("/v1/projects/j-off/config/groundedness",
                     json={"mode": "off", "judge": LOCAL})
    assert await evaluate_groundedness(db_session) == 0
    assert judge.calls == []


@pytest.mark.anyio
async def test_metadata_capture_never_calls_judge(client, judge):
    await client.put("/v1/privacy/projects/j-meta", json={"capture_mode": "metadata"})
    ids = await _seed(client, "j-meta")
    got = await _evaluate(client, "j-meta", {"mode": "judge", "judge": LOCAL}, [ids["invented"]])
    assert got[ids["invented"]]["status"] == "unknown"
    assert got[ids["invented"]]["reason"] == "metadata_capture"
    assert judge.calls == []


@pytest.mark.anyio
async def test_remote_judge_needs_allow_remote(client, judge):
    ids = await _seed(client, "j-remote")
    remote = {"provider": "openai-compatible", "model": "m", "base_url": "https://judge.example/v1"}
    got = await _evaluate(client, "j-remote", {"mode": "judge", "judge": remote}, [ids["twin"]])
    assert got[ids["twin"]]["reason"] == "judge_remote_not_allowed" and judge.calls == []
    got = await _evaluate(client, "j-remote", {"mode": "judge", "judge": {**remote,
                                                                           "allow_remote": True}},
                          [ids["twin"]])
    assert got[ids["twin"]]["status"] == "grounded" and len(judge.calls) == 1


@pytest.mark.anyio
async def test_a_pass_sends_at_most_max_per_pass_traces_to_the_judge(client, db_session, judge):
    await _seed(client, "j-cap")
    await client.put("/v1/projects/j-cap/config/groundedness",
                     json={"mode": "judge", "judge": LOCAL, "max_per_pass": 2})
    rows = await groundedness.evaluate_project(db_session, "j-cap", days=1, now=None)
    assert len(rows) <= 2 and len(judge.calls) <= 2


@pytest.mark.anyio
async def test_a_claim_the_reply_does_not_make_is_dropped(client, monkeypatch):
    # live run with llama3.2:3b: the user's question was listed as an unsupported claim of the twin
    ids = await _seed(client, "j-drop")
    monkeypatch.setattr(groundedness, "ask_judge", FakeJudge(answer=json.dumps(
        {"claims": 3, "level": "ungrounded", "unsupported": [{"claim": "where is my refund"}]})))
    got = await _evaluate(client, "j-drop", {"mode": "judge", "judge": LOCAL}, [ids["twin"]])
    row = got[ids["twin"]]
    assert row["status"] == "grounded" and row["judge_claims_dropped"] == 1


def test_a_quoted_number_is_a_claim_of_the_reply():
    # live run: the judge quoted only «180 USD» (no word of four letters) and it was dropped
    verdict = {"status": "ungrounded", "score": 0.6667, "claims": 3, "supported": 2,
               "unsupported": [{"claim": "180 USD", "why": "contradicted", "detail": "judge"}],
               "unsupported_count": 1}
    kept = groundedness._keep_claims_of_the_reply(verdict, INVENTED)
    assert kept["status"] == "ungrounded" and kept["unsupported"][0]["claim"] == "180 USD"
