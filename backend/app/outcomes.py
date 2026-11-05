"""How a turn ended: its outcome, where that came from, and why (``observer.agent.v1``).

Derived on read from the turn's signals, never stored: spans arrive in several batches and the
rules can improve without a backfill. In order, the first rule that applies wins:

1. ``observer.outcome`` on a span (the root span's value, else the one that ended last);
2. the ``kind`` of a ``close`` span (``abstain`` -> abstained, ...);
3. a trace in error -> ``failed``;
4. a degraded reply -> ``degraded``: the ManitOS flags, or the last LLM span cut off by its
   token limit (an early cut-off the agent recovered from does not count);
5. ``answered`` only with positive evidence: a ManitOS ``model.answered`` span, or the last LLM
   span finished with ``stop`` / ``end_turn`` on a trace that did not fail;
6. otherwise ``unknown`` (an ok trace is not taken as an answer).

The reason comes from ``observer.outcome.reason``, else the close span's ``reason_code``, else, for
a turn that did not answer, the reason codes of the last gate that stopped it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_contract import CLOSE_KINDS, OUTCOMES, UNKNOWN

if TYPE_CHECKING:
    from app.metrics_core import TurnSignals

_ANSWER_FINISH = {"stop", "end_turn", "stop_sequence", "eos"}
_CUT_FINISH = {"length", "max_tokens"}
_NOT_ANSWERED = {"abstained", "clarification", "refused", "failed"}


def derive_outcome(s: TurnSignals) -> tuple[str, str, str]:
    """``(outcome, source, reason)``; ``source`` is attribute, close, status, flags, evidence or
    none."""
    outcome, source = UNKNOWN, "none"
    if s.declared_outcome and s.declared_outcome[2] in OUTCOMES:
        outcome, source = s.declared_outcome[2], "attribute"
    elif s.close_kind in CLOSE_KINDS:
        outcome, source = CLOSE_KINDS[s.close_kind], "close"
    elif s.status == "error":
        outcome, source = "failed", "status"
    elif _degraded(s):
        outcome, source = "degraded", "flags"
    elif "model.answered" in s.seen_spans or (
        s.llm_last is not None and set(s.llm_last[1]) & _ANSWER_FINISH
    ):
        outcome, source = "answered", "evidence"
    return outcome, source, _reason(s, outcome)


def _degraded(s: TurnSignals) -> bool:
    if "manitos.turn.lifecycle" in s.seen_spans:
        return s.degraded or s.truncated
    return s.degraded or (s.llm_last is not None and bool(set(s.llm_last[1]) & _CUT_FINISH))


def _reason(s: TurnSignals, outcome: str) -> str:
    if s.declared_outcome and s.declared_outcome[3]:
        return s.declared_outcome[3]
    if s.close_reason:
        return s.close_reason
    if outcome not in _NOT_ANSWERED:
        return ""
    stopped = [g for g in s.gates if g.get("status") == "stop"]
    if stopped:
        last = max(stopped, key=lambda g: g.get("order", 0))
        return ",".join(last.get("reason_codes") or []) or str(last.get("name") or "")
    return ""


def turn_outcome(s: TurnSignals) -> dict[str, Any]:
    """The outcome fields of one turn, as the API returns them."""
    return {
        "outcome": s.outcome,
        "outcome_source": s.outcome_source,
        "outcome_reason": s.outcome_reason,
        "abstained_with_evidence": s.abstained_with_evidence,
        "retrieval_passages": s.retrieval_passages,
        "gates": [{k: v for k, v in g.items() if k != "order"} for g in s.gates],
        "task": s.task,
    }
