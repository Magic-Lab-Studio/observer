"""Deterministic operational-health score for one Manitos turn (no model involved).

The LLM-as-judge rubric needs a model and reads spans' text; a Manitos turn already carries its
own quality signals in metadata (truncated, degraded, tool error, latency, time to first token,
fallback). This scores them with fixed weights so every turn gets an evaluation for free, and
models/providers can be compared without sending any content anywhere.

These signals say how the turn *ran*, not whether the answer was *right*. The result therefore has
two axes: ``health`` (this score) and ``correctness`` (unknown until a real signal exists). While
correctness is unknown the overall score is capped, so health alone never earns an "A".

Scores run from 0 to 1. Grades reuse the engine's boundaries (A >= 0.9 ... F) and a turn passes
at ``PASS_THRESHOLD`` (the default scale's 0.6) unless it failed outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.evaluators.rubric_engine import _GRADE_BOUNDARIES
from app.metrics_core import TurnSignals

RUBRIC = "manitos_turn@2"
# Rubrics this one replaces; the sweep removes their rows when it writes the new ones.
LEGACY_RUBRICS = ("manitos_turn@1",)
PASS_THRESHOLD = 0.6
# Highest overall score while correctness is unknown: just under the "A" boundary (0.90).
UNKNOWN_CORRECTNESS_CAP = 0.89

# (upper bound, score): the first bound the value does not exceed wins; beyond the last, `tail`.
_LATENCY_MS = ((5_000, 1.0), (15_000, 0.7), (30_000, 0.4))
_LATENCY_TAIL = 0.1
_TTFT_MS = ((2_000, 1.0), (6_000, 0.7), (12_000, 0.4))
_TTFT_TAIL = 0.2


@dataclass(frozen=True)
class Criterion:
    name: str
    weight: float
    score: float
    detail: str


def _stepped(value: float, steps: tuple[tuple[int, float], ...], tail: float) -> float:
    for bound, score in steps:
        if value <= bound:
            return score
    return tail


def grade_for(score: float) -> str:
    for boundary in _GRADE_BOUNDARIES:
        if score >= boundary["min"]:
            return str(boundary["grade"])
    return "F"


def score_turn(s: TurnSignals) -> dict[str, Any]:
    """Weighted score of a turn's signals; criteria without data are left out and re-weighted."""
    criteria = [
        Criterion(
            "reliability",
            0.30,
            0.0 if (s.status == "error" or s.tool_error) else 1.0,
            "error" if s.status == "error" else "tool error" if s.tool_error else "ok",
        ),
        Criterion(
            "complete", 0.20, 0.0 if s.truncated else 1.0, "truncated" if s.truncated else "ok"
        ),
        Criterion(
            "degradation", 0.20, 0.0 if s.degraded else 1.0, "degraded" if s.degraded else "ok"
        ),
        Criterion("fallback", 0.05, 0.5 if s.fallback else 1.0, "fallback" if s.fallback else "ok"),
    ]
    if s.duration_ms is not None:
        criteria.append(
            Criterion(
                "latency",
                0.20,
                _stepped(s.duration_ms, _LATENCY_MS, _LATENCY_TAIL),
                f"{round(s.duration_ms)} ms",
            )
        )
    if s.ttft_ms is not None:
        criteria.append(
            Criterion(
                "ttft", 0.05, _stepped(s.ttft_ms, _TTFT_MS, _TTFT_TAIL), f"{round(s.ttft_ms)} ms"
            )
        )
    total_weight = sum(c.weight for c in criteria)
    health = sum(c.weight * c.score for c in criteria) / total_weight
    score = min(health, UNKNOWN_CORRECTNESS_CAP)
    failed = s.status == "error" or s.tool_error
    answered = s.answered_by or s.model
    return {
        "rubric": RUBRIC,
        "score": round(score, 4),
        "grade": grade_for(score),
        "health": {"score": round(health, 4), "grade": grade_for(health)},
        "correctness": {"status": "unknown", "score": None},
        "passed": bool(health >= PASS_THRESHOLD and not failed),
        "criteria": [
            {"name": c.name, "weight": c.weight, "score": c.score, "detail": c.detail}
            for c in criteria
        ],
        "model": answered[:160],
        "provider": answered.split(" · ", 1)[0].strip().lower() if " · " in answered else "",
    }
