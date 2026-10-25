"""Deterministic operational-health score for any trace that is not a Manitos turn.

An agent instrumented with the SDK or OTLP has no lifecycle span, but every trace says whether it
failed (trace status, error spans), whether a model stopped early (an LLM finish reason ``length``
or ``max_tokens``) and how long it took. This scores those with fixed weights, the same way
``manitos_turn`` does: it says how the trace *ran*, not whether the answer was *right*, so
correctness stays unknown and the overall score is capped below an "A".
"""

from __future__ import annotations

from typing import Any

from app.evaluators.manitos_turn import (
    _LATENCY_MS,
    _LATENCY_TAIL,
    PASS_THRESHOLD,
    UNKNOWN_CORRECTNESS_CAP,
    Criterion,
    _stepped,
    grade_for,
)
from app.metrics_core import TurnSignals

RUBRIC = "generic_turn@1"


def score_trace(s: TurnSignals) -> dict[str, Any]:
    """Weighted score of a trace's signals; latency is left out (and re-weighted) when unknown."""
    if s.status == "error":
        reliability, detail = 0.0, "error"
    elif s.span_error:
        reliability, detail = 0.5, "span error"  # a step failed but the trace recovered
    else:
        reliability, detail = 1.0, "ok"
    criteria = [
        Criterion("reliability", 0.45, reliability, detail),
        Criterion(
            "complete", 0.30, 0.0 if s.truncated else 1.0, "truncated" if s.truncated else "ok"
        ),
    ]
    if s.duration_ms is not None:
        criteria.append(
            Criterion(
                "latency",
                0.25,
                _stepped(s.duration_ms, _LATENCY_MS, _LATENCY_TAIL),
                f"{round(s.duration_ms)} ms",
            )
        )
    total_weight = sum(c.weight for c in criteria)
    health = sum(c.weight * c.score for c in criteria) / total_weight
    score = min(health, UNKNOWN_CORRECTNESS_CAP)
    return {
        "rubric": RUBRIC,
        "score": round(score, 4),
        "grade": grade_for(score),
        "health": {"score": round(health, 4), "grade": grade_for(health)},
        "correctness": {"status": "unknown", "score": None},
        "passed": bool(health >= PASS_THRESHOLD and s.status != "error"),
        "criteria": [
            {"name": c.name, "weight": c.weight, "score": c.score, "detail": c.detail}
            for c in criteria
        ],
        "model": s.model[:160],
        "provider": s.provider,
    }
