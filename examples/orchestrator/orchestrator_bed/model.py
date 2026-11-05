"""Episodes of a synthetic agent orchestrator, each with the answer Observer should find.

The orchestrator plans, retrieves, passes its draft through gates and closes the turn in one of
the outcomes of the agent contract (``observer.agent.v1``). Everything is synthetic and
deterministic; no model is called. Each episode is a span tree with times in milliseconds from
the episode's start and a ``truth`` dict that the checks compare against Observer's answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpanSpec:
    key: str
    name: str
    span_type: str
    start_ms: float
    end_ms: float
    parent: str | None = None  # key of the parent span; None for the root
    status: str = "ok"
    attrs: dict[str, Any] = field(default_factory=dict)
    inp: dict[str, Any] | None = None  # content: only the SDK wire format (batch) carries it
    out: dict[str, Any] | None = None


@dataclass
class Episode:
    key: str
    spans: list[SpanSpec]
    truth: dict[str, Any]
    offset_s: float = 0.0  # when the episode starts, from the bed's base time

    @property
    def status(self) -> str:
        root = next(s for s in self.spans if s.parent is None)
        return root.status


def _root(duration_ms: float, status: str = "ok", **attrs: Any) -> SpanSpec:
    return SpanSpec("root", "orchestrator.turn", "agent", 0, duration_ms, status=status,
                    attrs={"observer.task": "support_answer", **attrs})


def _plan(start: float = 10, end: float = 120) -> SpanSpec:
    return SpanSpec("plan", "plan", "decision", start, end, "root",
                    attrs={"decision.kind": "route", "decision.options": ["billing", "orders"],
                           "decision.choice": "orders",
                           "decision.rejected": ["billing:low_score"]})


def _llm(key: str, start: float, end: float, finish: str = "stop", status: str = "ok") -> SpanSpec:
    return SpanSpec(key, "chat", "llm", start, end, "root", status=status,
                    attrs={"gen_ai.operation.name": "chat",
                           "gen_ai.request.model": "llama3.2:3b",
                           "gen_ai.provider.name": "ollama",
                           "gen_ai.response.finish_reasons": [finish]})


def _retrieval(passages: int, start: float = 130, end: float = 400) -> SpanSpec:
    return SpanSpec("search", "kb.search", "retrieval", start, end, "root",
                    attrs={"retrieval.name": "kb", "retrieval.passages": passages,
                           "retrieval.sources": [f"kb:{i}" for i in range(passages)]})


def _gate(key: str, name: str, status: str, start: float, codes: list[str] | None = None) -> SpanSpec:
    attrs: dict[str, Any] = {"gate.name": name, "gate.stage": name, "gate.status": status}
    if codes:
        attrs["gate.reason_codes"] = codes
    return SpanSpec(key, f"gate.{name}", "gate", start, start + 15, "root", attrs=attrs)


def _close(kind: str, start: float, reason: str | None = None) -> SpanSpec:
    attrs = {"close.kind": kind}
    if reason:
        attrs["close.reason_code"] = reason
    return SpanSpec("close", "close", "close", start, start + 5, "root", attrs=attrs)


def outcome_episodes() -> list[Episode]:
    """Step 10: one episode per outcome, plus the ways an outcome is read."""
    def truth(outcome: str, source: str, reason: str = "", evidence: bool = False) -> dict:
        return {"outcome": outcome, "outcome_source": source, "outcome_reason": reason,
                "abstained_with_evidence": evidence}

    return [
        Episode("answered", [_root(1500), _plan(), _retrieval(3), _llm("llm", 420, 1400),
                             _gate("g1", "verification", "pass", 1410)],
                truth("answered", "evidence")),
        Episode("degraded", [_root(2100), _plan(), _llm("llm", 130, 2000, finish="length")],
                truth("degraded", "flags")),
        Episode("abstained_with_evidence",
                [_root(900), _plan(), _retrieval(4),
                 _gate("g1", "coverage", "stop", 410, ["coverage_below_min"]),
                 _close("abstain", 880)],
                truth("abstained", "close", "coverage_below_min", evidence=True)),
        Episode("abstained_no_results",
                [_root(700), _plan(), _retrieval(0), _close("abstain", 680, "no_results")],
                truth("abstained", "close", "no_results")),
        Episode("clarification",
                [_root(600), _plan(), _close("clarify", 580, "ambiguous_entity")],
                truth("clarification", "close", "ambiguous_entity")),
        Episode("refused", [_root(400), _close("refuse", 380, "policy_block")],
                truth("refused", "close", "policy_block")),
        Episode("failed", [_root(1200, status="error"), _plan(),
                           _llm("llm", 130, 1100, finish="error", status="error")],
                truth("failed", "status")),
        # the user stopped it: the transport says error, the close step says why
        Episode("cancelled", [_root(800, status="error"), _plan(),
                              _llm("llm", 130, 790, finish="error", status="error"),
                              _close("cancel", 792, "user_stop")],
                truth("cancelled", "close", "user_stop")),
        Episode("declared", [_root(300, **{"observer.outcome": "answered",
                                           "observer.outcome.reason": "cache_hit"})],
                truth("answered", "attribute", "cache_hit")),
        Episode("unknown", [_root(500),
                            SpanSpec("tool", "lookup", "tool", 50, 450, "root",
                                     attrs={"tool.name": "lookup", "tool.arg_names": ["id"]})],
                truth("unknown", "none")),
    ]


ORDER_RECORD = ("Order 4471 shipped on 3 March from the Bogota warehouse. Its refund is 120 USD "
                "and reaches the card in 14 days.")


def groundedness_episodes() -> list[Episode]:
    """Step 9: a reply that invents a number its tool output does not contain, and its correct
    twin. Both answered; only the content tells them apart."""
    def episode(key: str, refund: int, truth: dict[str, Any]) -> Episode:
        reply = (f"Your order 4471 shipped on 3 March. The refund is {refund} USD and reaches "
                 "your card in 14 days. Anything else?")
        root = _root(1300)
        root.inp, root.out = {"user": "when do I get my refund for 4471?"}, {"reply": reply}
        tool = SpanSpec("tool", "orders.lookup", "tool", 130, 400, "root",
                        attrs={"tool.name": "orders.lookup", "tool.arg_names": ["order_id"]},
                        inp={"order_id": "4471"}, out={"record": ORDER_RECORD})
        return Episode(key, [root, _plan(), tool, _llm("llm", 420, 1250)],
                       {"outcome": "answered", "outcome_source": "evidence", "outcome_reason": "",
                        "abstained_with_evidence": False, **truth})

    return [episode("grounded_reply", 120, {}), episode("invented_number", 180, {})]


# Planted groundedness of the step-9 episodes when content was stored (batch format).
GROUNDEDNESS_TRUTH = {"grounded_reply": ("grounded", None), "invented_number": ("ungrounded", "180")}


def build_episodes(seed: int = 7) -> list[Episode]:
    """Every planted episode, spaced a few seconds apart (``seed`` is reserved for variation)."""
    episodes = outcome_episodes() + groundedness_episodes()
    for i, ep in enumerate(episodes):
        ep.offset_s = i * 3.0
    return episodes
