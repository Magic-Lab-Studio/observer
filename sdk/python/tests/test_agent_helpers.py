"""The agent contract helpers (llm_observatory.agent) write the observer.agent.v1 keys."""

import pytest

from llm_observatory import Tracer, agent, set_tracer, trace


@pytest.fixture
def tracer():
    t = Tracer(service_name="helpers", attributes={"vcs.commit": "abc123"})
    set_tracer(t)
    yield t
    set_tracer(Tracer())


def _by_name(tracer):
    return {s["name"]: s for s in tracer.export()}


def test_an_abstention_is_recorded_with_its_gate_and_close(tracer):
    @trace(name="turn", span_type="agent")
    def turn():
        agent.set_task("support_answer")
        agent.set_user("u-41")
        with agent.retrieval("kb") as r:
            r.found(4, sources=["kb:12", "kb:40"], top_score=0.31)
        with agent.gate("coverage", requirement="min_passages=5") as g:
            g.stop("coverage_below_min")
        agent.close("abstain")

    turn()
    spans = _by_name(tracer)
    root = spans["turn"]
    assert root["attributes"]["observer.task"] == "support_answer"
    assert root["attributes"]["user.id"] == "u-41"
    search, gate, close = spans["retrieval.kb"], spans["gate.coverage"], spans["close"]
    assert {search["parent_id"], gate["parent_id"], close["parent_id"]} == {root["id"]}
    assert search["span_type"] == "retrieval" and search["attributes"]["retrieval.passages"] == 4
    assert search["attributes"]["retrieval.sources"] == ["kb:12", "kb:40"]
    assert gate["span_type"] == "gate"
    assert gate["attributes"]["gate.status"] == "stop"
    assert gate["attributes"]["gate.reason_codes"] == ["coverage_below_min"]
    assert close["span_type"] == "close" and close["attributes"]["close.kind"] == "abstain"


def test_tracer_attributes_travel_on_every_span_and_a_span_value_wins(tracer):
    @trace(name="turn", span_type="agent")
    def turn():
        with agent.decision("route", ["billing", "orders"]) as d:
            d.choose("orders", rejected={"billing": "low_score"}, confidence=0.8)

    turn()
    spans = _by_name(tracer)
    assert all(s["attributes"]["vcs.commit"] == "abc123" for s in spans.values())
    attrs = spans["decision.route"]["attributes"]
    assert attrs["decision.choice"] == "orders"
    assert attrs["decision.rejected"] == ["billing:low_score"]
    assert attrs["decision.options"] == ["billing", "orders"]


def test_a_gate_without_a_verdict_passed_and_a_retrieval_without_results_found_none(tracer):
    @trace(name="turn", span_type="agent")
    def turn():
        with agent.gate("policy"):
            pass
        with agent.retrieval("kb"):
            pass

    turn()
    spans = _by_name(tracer)
    assert spans["gate.policy"]["attributes"]["gate.status"] == "pass"
    assert spans["retrieval.kb"]["attributes"]["retrieval.passages"] == 0


def test_an_error_inside_a_block_marks_the_span_and_propagates(tracer):
    @trace(name="turn", span_type="agent")
    def turn():
        with agent.tool_call("lookup", ["order_id"]):
            raise TimeoutError("slow")

    with pytest.raises(TimeoutError):
        turn()
    tool = _by_name(tracer)["lookup"]
    assert tool["status"] == "error" and tool["attributes"]["tool.arg_names"] == ["order_id"]


def test_a_tool_error_code_stays_an_error(tracer):
    @trace(name="turn", span_type="agent")
    def turn():
        with agent.tool_call("lookup") as call:
            call.result(error_code="not_found")
        with agent.verification() as v:
            v.result(3, rejected=1, reason_codes=["claim_unsupported"])

    turn()
    spans = _by_name(tracer)
    assert spans["lookup"]["status"] == "error"
    assert spans["lookup"]["attributes"]["tool.error_code"] == "not_found"
    ver = spans["verification.claims"]["attributes"]
    assert (ver["verification.claims_accepted"], ver["verification.claims_rejected"]) == (3, 1)


def test_outcome_rejects_unknown_values_and_needs_a_span(tracer):
    @trace(name="turn", span_type="agent")
    def turn():
        agent.outcome("answered", "cache_hit")
        with pytest.raises(ValueError):
            agent.outcome("great")
        with pytest.raises(ValueError):
            agent.close("maybe")

    turn()
    root = _by_name(tracer)["turn"]["attributes"]
    assert (root["observer.outcome"], root["observer.outcome.reason"]) == ("answered", "cache_hit")
    with pytest.raises(RuntimeError):
        agent.outcome("answered")  # no current span outside a traced function


def test_the_sdk_constants_match_the_contract():
    assert agent.OUTCOMES == (
        "answered", "degraded", "abstained", "clarification", "refused", "failed")
    assert agent.CLOSE_KINDS == ("answer", "clarify", "abstain", "refuse", "fail")
