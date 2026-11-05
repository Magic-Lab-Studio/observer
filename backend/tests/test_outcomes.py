"""How a turn ended (app.outcomes): the rules, their order, and what counts as evidence."""

from datetime import datetime, timedelta, timezone

from app.metrics_core import TurnSignals, _apply_agent_span, _apply_genai_span, _finish

T0 = datetime(2026, 11, 3, tzinfo=timezone.utc)


def _turn(*spans, status="ok", **flags) -> TurnSignals:
    """spans: (attrs, span_type, is_root, start_offset_s, end_offset_s)."""
    s = TurnSignals(trace_id="t", status=status, **flags)
    for attrs, span_type, is_root, a, b in spans:
        _apply_genai_span(s, attrs, "", T0 + timedelta(seconds=a))
        _apply_agent_span(
            s,
            attrs,
            span_type=span_type,
            is_root=is_root,
            start=T0 + timedelta(seconds=a),
            end=T0 + timedelta(seconds=b),
        )
    _finish(s)
    return s


def _close(kind, reason="", at=5):
    return ({"close.kind": kind, "close.reason_code": reason}, "close", False, at, at + 0.1)


def _llm(reason="stop", at=1):
    return ({"gen_ai.response.finish_reasons": [reason]}, "llm", False, at, at + 1)


def test_attribute_outcome_wins_over_rules():
    s = _turn(({"observer.outcome": "refused"}, "agent", True, 0, 9), _close("abstain"),
              status="error")
    assert (s.outcome, s.outcome_source) == ("refused", "attribute")


def test_root_attribute_wins_over_a_later_child():
    s = _turn(({"observer.outcome": "answered"}, "agent", True, 0, 3),
              ({"observer.outcome": "failed"}, "step", False, 1, 8))
    assert s.outcome == "answered"


def test_invalid_outcome_value_is_ignored():
    s = _turn(({"observer.outcome": "great"}, "agent", True, 0, 3), _llm())
    assert (s.outcome, s.outcome_source) == ("answered", "evidence")


def test_close_kinds_map_to_outcomes():
    for kind, outcome in [("clarify", "clarification"), ("abstain", "abstained"),
                          ("refuse", "refused"), ("answer", "answered"), ("fail", "failed"),
                          ("cancel", "cancelled")]:
        s = _turn(_close(kind, "why"))
        assert (s.outcome, s.outcome_source, s.outcome_reason) == (outcome, "close", "why")


def test_a_turn_the_user_stopped_is_cancelled_not_failed():
    # the transport marks a stopped turn as an error; the agent's word decides
    declared = _turn(({"observer.outcome": "cancelled", "observer.outcome.reason": "user_stop"},
                      "agent", True, 0, 2), _llm(), status="error")
    assert (declared.outcome, declared.outcome_reason) == ("cancelled", "user_stop")
    closed = _turn(_llm(), _close("cancel", "user_stop"), status="error")
    assert (closed.outcome, closed.outcome_source) == ("cancelled", "close")


def test_error_trace_is_failed():
    assert _turn(_llm(), status="error").outcome == "failed"


def test_degraded_or_truncated_is_degraded():
    assert _turn(_llm(), degraded=True).outcome == "degraded"
    assert _turn(_llm("length")).outcome == "degraded"  # a truncated LLM reply


def test_ok_trace_without_evidence_is_unknown():
    s = _turn(({}, "agent", True, 0, 1))
    assert (s.outcome, s.outcome_source) == ("unknown", "none")


def test_answered_requires_positive_evidence():
    assert _turn(_llm("stop")).outcome == "answered"
    assert _turn(_llm("tool_calls")).outcome == "unknown"
    s = TurnSignals(trace_id="m", seen_spans={"model.answered"})
    _finish(s)
    assert s.outcome == "answered"


def test_reason_from_last_stopping_gate():
    s = _turn(
        ({"gate.name": "coverage", "gate.status": "stop", "gate.reason_codes": ["low"]},
         "gate", False, 1, 2),
        ({"gate.name": "verification", "gate.status": "stop",
          "gate.reason_codes": "claim_unsupported"}, "gate", False, 3, 4),
        ({"observer.outcome": "abstained"}, "agent", True, 0, 5),
    )
    assert s.outcome_reason == "claim_unsupported"
    assert [g["name"] for g in s.gates] == ["coverage", "verification"]


def test_abstained_with_evidence_needs_passages():
    found = _turn(({"retrieval.passages": 4}, "retrieval", False, 0, 1), _close("abstain"))
    empty = _turn(({"retrieval.passages": 0}, "retriever", False, 0, 1), _close("abstain"))
    answered = _turn(({"retrieval.passages": 4}, "retrieval", False, 0, 1), _llm())
    assert found.abstained_with_evidence and found.retrieval_passages == 4
    assert not empty.abstained_with_evidence and not answered.abstained_with_evidence


def test_otlp_declared_type_counts_as_retrieval():
    s = _turn(({"observer.span.type": "retrieval", "retrieval.passages": "3"}, "generic", False,
               0, 1), _close("abstain"))
    assert s.retrieval_passages == 3 and s.abstained_with_evidence


def test_a_recovered_cut_off_is_answered_not_degraded():
    s = _turn(_llm("length", at=1), _llm("stop", at=3))
    assert (s.outcome, s.outcome_source) == ("answered", "evidence")


def test_close_span_that_ended_last_wins_whatever_the_row_order():
    first, second = _close("abstain", "low", at=2), _close("answer", at=6)
    assert _turn(first, second).outcome == "answered"
    assert _turn(second, first).outcome == "answered"


def test_an_answered_turn_carries_no_stale_gate_reason():
    s = _turn(({"gate.name": "coverage", "gate.status": "stop", "gate.reason_codes": ["low"]},
               "gate", False, 1, 2), _llm("stop", at=4))
    assert s.outcome == "answered" and s.outcome_reason == ""


def test_abstained_with_evidence_rate_is_a_share_of_abstentions():
    from app.metrics_core import compute_metric

    with_evidence = _turn(({"retrieval.passages": 2}, "retrieval", False, 0, 1), _close("abstain"))
    answered = [_turn(_llm()) for _ in range(9)]
    assert compute_metric("abstained_with_evidence_rate", [with_evidence, *answered]) == (1.0, 1)
    assert compute_metric("abstained_with_evidence_rate", answered) == (None, 0)


def test_an_unknown_outcome_metric_is_an_error():
    import pytest

    from app.metrics_core import compute_metric

    with pytest.raises(ValueError):
        compute_metric("outcome_answerd_rate", [_turn(_llm())])
