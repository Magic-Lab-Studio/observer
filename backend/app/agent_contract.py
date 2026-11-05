"""The agent span contract, ``observer.agent.v1`` (documented in ``docs/agent-spans.md``).

An agent tells Observer *why* a turn ended the way it did with plain span attributes, over any
ingestion path (the SDK batch, OTLP, the ManitOS envelope):

* the turn's **outcome** (``observer.outcome``: answered, degraded, abstained, clarification,
  refused, failed) and its reason (``observer.outcome.reason``);
* typed **agent spans**: ``decision``, ``tool``, ``retrieval``, ``gate``, ``verification``,
  ``replan`` and ``close``, each with a fixed set of keys.

Every key is metadata (names, codes, counts, ids), so it survives the ``metadata`` capture mode;
a test keeps it that way. Unknown keys are ignored, never rejected: the contract only grows.
"""

from __future__ import annotations

from typing import Any

VERSION = "observer.agent.v1"

OUTCOMES = ("answered", "degraded", "abstained", "clarification", "refused", "failed")
UNKNOWN = "unknown"

# ``close.kind`` -> outcome
CLOSE_KINDS = {
    "answer": "answered",
    "clarify": "clarification",
    "abstain": "abstained",
    "refuse": "refused",
    "fail": "failed",
}

AGENT_SPAN_TYPES = ("decision", "tool", "retrieval", "gate", "verification", "replan", "close")
# Span types other conventions produce for the same thing (OpenInference ``RETRIEVER``).
ALIASES = {"retriever": "retrieval"}

# The attribute naming a span's contract type when the transport has no span type (OTLP).
SPAN_TYPE_ATTR = "observer.span.type"

TURN_KEYS = frozenset(
    {
        "observer.outcome",
        "observer.outcome.reason",
        "observer.task",
        "observer.depends_on",
        SPAN_TYPE_ATTR,
        "user.id",
        "enduser.id",
    }
)

KEYS: dict[str, frozenset[str]] = {
    "decision": frozenset(
        f"decision.{k}"
        for k in ("kind", "options", "choice", "rejected", "scores", "confidence", "final_by")
    ),
    "tool": frozenset(
        f"tool.{k}"
        for k in ("name", "arg_names", "result_size", "error_code", "args_digest", "call_id")
    ),
    "retrieval": frozenset(
        f"retrieval.{k}" for k in ("name", "passages", "sources", "top_score", "index")
    ),
    "gate": frozenset(
        f"gate.{k}" for k in ("name", "stage", "status", "requirement", "reason_codes")
    ),
    "verification": frozenset(
        f"verification.{k}"
        for k in ("claims_accepted", "claims_rejected", "claims_pruned", "reason_codes")
    ),
    "replan": frozenset(f"replan.{k}" for k in ("reason", "attempt")),
    "close": frozenset(f"close.{k}" for k in ("kind", "reason_code")),
}

GATE_STATUSES = ("pass", "stop", "skip")


def contract_type(span_type: str | None, attrs: dict[str, Any] | None) -> str | None:
    """The span's contract type: ``observer.span.type`` first, then its own type (with aliases)."""
    declared = str((attrs or {}).get(SPAN_TYPE_ATTR) or "").strip().lower()
    for value in (declared, str(span_type or "").strip().lower()):
        value = ALIASES.get(value, value)
        if value in AGENT_SPAN_TYPES:
            return value
    return None


def codes(value: Any) -> list[str]:
    """Reason codes as a list of short strings, from a list or a comma-separated string."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [str(v).strip()[:80] for v in value if str(v).strip()][:16]


def describe() -> dict[str, Any]:
    """The contract as data (``GET /v1/agent/contract``)."""
    return {
        "version": VERSION,
        "outcomes": list(OUTCOMES),
        "unknown": UNKNOWN,
        "close_kinds": dict(CLOSE_KINDS),
        "span_types": list(AGENT_SPAN_TYPES),
        "aliases": dict(ALIASES),
        "span_type_attribute": SPAN_TYPE_ATTR,
        "turn_keys": sorted(TURN_KEYS),
        "keys": {k: sorted(v) for k, v in KEYS.items()},
        "gate_statuses": list(GATE_STATUSES),
    }
