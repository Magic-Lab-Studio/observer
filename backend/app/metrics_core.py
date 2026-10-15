"""Turn-level signals and statistics shared by alerts (and, later, the metrics query).

A Manitos turn is one trace. Its quality signals live in span attributes: the lifecycle span
(``manitos.turn.lifecycle``) carries duration, time to first token and the degraded/truncated/
tool-error flags, ``llm.chain`` carries the provider attempts, ``model_routing`` the intent and
the local-fallback flag. Everything is aggregated in Python over a bounded window, like
``/v1/analytics/manitos-quality`` does, so it behaves the same on SQLite and PostgreSQL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trace import Span, Trace

ROW_LIMIT = 20000
_CHUNK = 500

# metric id -> (label, unit, kind). kind: "turns" (needs min samples), "provider" (llm.chain
# attempts), "quota" (provider snapshot; no sampling).
METRICS: dict[str, dict[str, Any]] = {
    "error_rate": {"label": "Tasa de error", "unit": "ratio", "kind": "turns"},
    "p50_latency_ms": {"label": "Latencia p50", "unit": "ms", "kind": "turns"},
    "p95_latency_ms": {"label": "Latencia p95", "unit": "ms", "kind": "turns"},
    "fallback_rate": {"label": "Turnos en vía alterna", "unit": "ratio", "kind": "turns"},
    "degraded_rate": {"label": "Respuestas degradadas", "unit": "ratio", "kind": "turns"},
    "truncated_rate": {"label": "Respuestas truncadas", "unit": "ratio", "kind": "turns"},
    "provider_failure_rate": {
        "label": "Fallos de proveedor (429, timeout, error)",
        "unit": "ratio",
        "kind": "provider",
    },
    "provider_quota_out": {
        "label": "Proveedores sin cuota",
        "unit": "count",
        "kind": "quota",
    },
}


@dataclass
class TurnSignals:
    trace_id: str
    status: str = "ok"
    created_at: datetime | None = None
    duration_ms: float | None = None
    ttft_ms: float | None = None
    degraded: bool = False
    truncated: bool = False
    tool_error: bool = False
    fallback: bool = False
    tts_error: bool = False
    model: str = ""
    language: str = ""
    intent: str = ""
    answered_by: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    seen_spans: set[str] = field(default_factory=set)
    environment: str = ""
    session_id: str = ""


def aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (the same rule the providers page and the dashboard use)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))]


def parse_attempts(text: str) -> list[dict[str, Any]]:
    """Parse ``groq:qwen/qwen3.8-27b=429@412ms>llamacpp:-=ok`` into attempt dicts.

    Each is ``{backend, model, result, ms}``; unreadable parts are skipped.
    """
    out: list[dict[str, Any]] = []
    for part in (text or "").split(">"):
        head, sep, tail = part.rpartition("=")
        if not sep or ":" not in head:
            continue
        backend, _, model = head.partition(":")
        ms = None
        if "@" in tail and tail.endswith("ms"):
            tail, _, raw = tail.rpartition("@")
            try:
                ms = int(raw[:-2])
            except ValueError:
                ms = None
        backend = backend.strip().lower()
        if not backend or not tail:
            continue
        out.append(
            {"backend": backend, "model": "" if model == "-" else model, "result": tail, "ms": ms}
        )
    return out


def classify_attempt(result: str) -> str:
    if result == "ok":
        return "ok"
    if result == "429":
        return "rate_limited"
    if result == "timeout":
        return "timeout"
    if result.startswith("skipped"):
        return "skipped"
    return "error"


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


async def load_turn_signals(
    session: AsyncSession,
    *,
    project_id: str,
    since: datetime,
    environment: str | None = None,
    limit: int = ROW_LIMIT,
) -> list[TurnSignals]:
    """Signals of every turn created at or after ``since`` (newest first, capped at ``limit``)."""
    query = (
        select(
            Trace.id,
            Trace.status,
            Trace.created_at,
            Trace.start_time,
            Trace.end_time,
            Trace.environment,
            Trace.session_id,
        )
        .where(Trace.created_at >= since, Trace.project_id == project_id)
        .order_by(Trace.created_at.desc())
        .limit(limit)
    )
    if environment:
        query = query.where(Trace.environment == environment)
    rows = (await session.execute(query)).all()
    return await _signals_from_rows(session, rows)


async def load_signals_for(session: AsyncSession, trace_ids: list[str]) -> list[TurnSignals]:
    """Signals of the given traces (used by the evaluation sweep)."""
    rows: list = []
    for i in range(0, len(trace_ids), _CHUNK):
        chunk = trace_ids[i : i + _CHUNK]
        rows += (
            await session.execute(
                select(
                    Trace.id,
                    Trace.status,
                    Trace.created_at,
                    Trace.start_time,
                    Trace.end_time,
                    Trace.environment,
                    Trace.session_id,
                ).where(Trace.id.in_(chunk))
            )
        ).all()
    return await _signals_from_rows(session, rows)


async def _signals_from_rows(session: AsyncSession, rows: list) -> list[TurnSignals]:
    turns: dict[str, TurnSignals] = {}
    for tid, status, created, start, end, env, session_id in rows:
        signals = TurnSignals(
            trace_id=str(tid),
            status=str(status or "ok"),
            created_at=aware(created),
            environment=str(env or ""),
            session_id=str(session_id or ""),
        )
        if start is not None and end is not None:
            signals.duration_ms = (aware(end) - aware(start)).total_seconds() * 1000.0
        turns[signals.trace_id] = signals
    ids = list(turns)
    for i in range(0, len(ids), _CHUNK):
        span_rows = await session.execute(
            select(Span.trace_id, Span.name, Span.attributes).where(
                Span.trace_id.in_(ids[i : i + _CHUNK])
            )
        )
        for trace_id, name, attrs in span_rows.all():
            signals = turns.get(str(trace_id))
            if signals is None:
                continue
            signals.seen_spans.add(str(name))
            _apply_span(signals, str(name), attrs if isinstance(attrs, dict) else {})
    return list(turns.values())


def _apply_span(signals: TurnSignals, name: str, attrs: dict[str, Any]) -> None:
    if name == "manitos.turn.lifecycle":
        signals.degraded = attrs.get("llm_degraded") is True
        signals.truncated = attrs.get("llm_truncated") is True
        signals.tool_error = attrs.get("tool_error") is True
        duration = _number(attrs.get("duration_ms"))
        if duration is not None:
            signals.duration_ms = duration
        signals.ttft_ms = _number(attrs.get("ttft_ms"))
        if not signals.model:
            signals.model = str(attrs.get("model") or "").strip()[:160]
    if attrs.get("local_fallback") is True:
        signals.fallback = True
    if name == "tts.synthesis" and attrs.get("status") == "error":
        signals.tts_error = True
    if name == "model.answered":
        signals.model = str(attrs.get("model") or "").strip()[:160] or signals.model
    if name == "model_routing":
        signals.intent = str(attrs.get("intent") or "").strip()[:80]
    if name == "llm.chain":
        signals.answered_by = str(attrs.get("answered_by") or "")
        signals.attempts = parse_attempts(str(attrs.get("attempts_text") or ""))
    language = str(attrs.get("language") or "").strip()[:160]
    if language and not signals.language:
        signals.language = language


def compute_metric(
    metric: str, signals: list[TurnSignals], *, scope: str = ""
) -> tuple[float | None, int]:
    """``(value, samples)`` of a turns/provider metric. ``value`` is None without any sample."""
    total = len(signals)
    if metric in {"error_rate", "fallback_rate", "degraded_rate", "truncated_rate"}:
        if not total:
            return None, 0
        count = {
            "error_rate": sum(1 for s in signals if s.status == "error"),
            "fallback_rate": sum(1 for s in signals if s.fallback),
            "degraded_rate": sum(1 for s in signals if s.degraded),
            "truncated_rate": sum(1 for s in signals if s.truncated),
        }[metric]
        return count / total, total
    if metric in {"p50_latency_ms", "p95_latency_ms"}:
        durations = [s.duration_ms for s in signals if s.duration_ms is not None]
        p = 0.5 if metric == "p50_latency_ms" else 0.95
        return percentile(durations, p), len(durations)
    if metric == "provider_failure_rate":
        tried = failed = 0
        for s in signals:
            for a in s.attempts:
                if scope and a["backend"] != scope:
                    continue
                kind = classify_attempt(a["result"])
                if kind == "skipped":
                    continue
                tried += 1
                failed += kind in {"rate_limited", "timeout", "error"}
        return (failed / tried if tried else None), tried
    raise ValueError(f"not a turn metric: {metric}")
