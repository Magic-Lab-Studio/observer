"""Turn-level signals and statistics shared by alerts (and, later, the metrics query).

A Manitos turn is one trace. Its quality signals live in span attributes: the lifecycle span
(``manitos.turn.lifecycle``) carries duration, time to first token and the degraded/truncated/
tool-error flags, ``llm.chain`` carries the provider attempts, ``model_routing`` the intent and
the local-fallback flag. Everything is aggregated in Python over a bounded window, like
``/v1/analytics/manitos-quality`` does, so it behaves the same on SQLite and PostgreSQL.

Any other trace (the Python SDK, OTLP) is a turn too: its duration and status come from the trace,
the model from its LLM spans' GenAI attributes and truncation from their finish reasons.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_contract import OUTCOMES, UNKNOWN, codes, contract_type
from app.models.trace import Span, Trace
from app.outcomes import derive_outcome

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
    "ingest_silence_minutes": {
        "label": "Minutos sin recibir trazas",
        "unit": "count",
        "kind": "ingest",
    },
    "provider_quota_out": {
        "label": "Proveedores sin cuota",
        "unit": "count",
        "kind": "quota",
    },
    # Outcomes of the agent contract (observer.agent.v1); every turn counts in the denominator,
    # so the rates add up to 1 with outcome_unknown_rate. abstained_with_evidence_rate is a share
    # of the abstentions.
    "outcome_answered_rate": {"label": "Turnos respondidos", "unit": "ratio", "kind": "turns"},
    "outcome_degraded_rate": {"label": "Turnos degradados", "unit": "ratio", "kind": "turns"},
    "outcome_abstained_rate": {"label": "Turnos abstenidos", "unit": "ratio", "kind": "turns"},
    "outcome_clarification_rate": {
        "label": "Turnos que pidieron aclaración",
        "unit": "ratio",
        "kind": "turns",
    },
    "outcome_refused_rate": {"label": "Turnos rechazados", "unit": "ratio", "kind": "turns"},
    "outcome_failed_rate": {"label": "Turnos fallidos", "unit": "ratio", "kind": "turns"},
    "outcome_cancelled_rate": {"label": "Turnos cancelados", "unit": "ratio", "kind": "turns"},
    "outcome_unknown_rate": {
        "label": "Turnos sin resultado conocido",
        "unit": "ratio",
        "kind": "turns",
    },
    "abstained_with_evidence_rate": {
        "label": "Abstenciones que tenían evidencia",
        "unit": "ratio",
        "kind": "turns",
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
    provider: str = ""  # gen_ai.system of the LLM span the model was taken from
    span_error: bool = False
    # (start, model, provider) of the latest LLM span; used only when no Manitos span names a model
    llm_pick: tuple[datetime, str, str] | None = field(default=None, repr=False, compare=False)
    llm_truncated: bool = False
    # Agent contract (observer.agent.v1): how the turn ended and why
    outcome: str = UNKNOWN
    outcome_source: str = "none"
    outcome_reason: str = ""
    retrieval_passages: int = 0
    abstained_with_evidence: bool = False
    gates: list[dict[str, Any]] = field(default_factory=list)
    task: str = ""
    # (is_root, end, outcome, reason) of the best span declaring observer.outcome
    declared_outcome: tuple[bool, datetime, str, str] | None = field(
        default=None, repr=False, compare=False
    )
    close_kind: str = ""
    close_reason: str = ""
    # (is_root, end) of the close span chosen so far
    close_pick: tuple[bool, datetime] | None = field(default=None, repr=False, compare=False)
    # (end, finish_reasons) of the LLM span that ended last
    llm_last: tuple[datetime, list[str]] | None = field(default=None, repr=False, compare=False)


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
    until: datetime | None = None,
) -> list[TurnSignals]:
    """Signals of every turn created in ``[since, until]`` (newest first, capped at ``limit``).

    Both bounds are applied in SQL before the cap, so a historical range is never starved by
    newer rows.
    """
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
    if until is not None:
        query = query.where(Trace.created_at <= until)
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
        span_rows = await session.execute(span_signal_query(ids[i : i + _CHUNK]))
        for trace_id, name, attrs, status, start, parent, span_type, end in span_rows.all():
            signals = turns.get(str(trace_id))
            if signals is None:
                continue
            signals.seen_spans.add(str(name))
            attrs = attrs if isinstance(attrs, dict) else {}
            _apply_span(signals, str(name), attrs)
            _apply_genai_span(signals, attrs, str(status or ""), aware(start))
            _apply_agent_span(
                signals,
                attrs,
                span_type=str(span_type or ""),
                is_root=parent is None,
                start=aware(start),
                end=aware(end),
            )
    for signals in turns.values():
        _finish(signals)
    return list(turns.values())


def span_signal_query(trace_ids: list[str]):
    """The span columns turn signals read: scalars only, never a span's input or output."""
    return select(
        Span.trace_id,
        Span.name,
        Span.attributes,
        Span.status,
        Span.start_time,
        Span.parent_span_id,
        Span.span_type,
        Span.end_time,
    ).where(Span.trace_id.in_(trace_ids))


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _apply_agent_span(
    signals: TurnSignals,
    attrs: dict[str, Any],
    *,
    span_type: str,
    is_root: bool,
    start: datetime | None,
    end: datetime | None,
) -> None:
    """The agent contract's attributes (``app.agent_contract``)."""
    when = end or start or _EPOCH
    declared = str(attrs.get("observer.outcome") or "").strip().lower()
    if declared in OUTCOMES:
        candidate = (is_root, when, declared, str(attrs.get("observer.outcome.reason") or "")[:160])
        best = signals.declared_outcome
        if best is None or (candidate[0], candidate[1]) >= (best[0], best[1]):
            signals.declared_outcome = candidate
    task = str(attrs.get("observer.task") or "").strip()[:80]
    if task and (is_root or not signals.task):
        signals.task = task
    reasons = attrs.get("gen_ai.response.finish_reasons")
    if isinstance(reasons, str):
        reasons = [reasons]
    if isinstance(reasons, list) and (
        signals.llm_last is None or when >= signals.llm_last[0]
    ):
        signals.llm_last = (when, [str(r).lower() for r in reasons])
    kind = contract_type(span_type, attrs)
    if kind == "retrieval":
        signals.retrieval_passages += max(0, _int(attrs.get("retrieval.passages")) or 0)
    elif kind == "gate":
        signals.gates.append(
            {
                "name": str(attrs.get("gate.name") or "")[:80],
                "stage": str(attrs.get("gate.stage") or "")[:80],
                "status": str(attrs.get("gate.status") or "").lower()[:10],
                "reason_codes": codes(attrs.get("gate.reason_codes")),
                "order": start.timestamp() if start else 0.0,
            }
        )
    elif kind == "close":
        pick = (is_root, when)
        if signals.close_pick is None or pick >= signals.close_pick:
            signals.close_pick = pick
            signals.close_kind = str(attrs.get("close.kind") or "").strip().lower()
            signals.close_reason = str(attrs.get("close.reason_code") or "")[:160]


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


_TRUNCATED_FINISH = {"length", "max_tokens"}
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _apply_genai_span(
    signals: TurnSignals, attrs: dict[str, Any], status: str, start: datetime | None
) -> None:
    """Signals every instrumented trace has: span errors and the GenAI attributes of LLM spans."""
    if status == "error":
        signals.span_error = True
    reasons = attrs.get("gen_ai.response.finish_reasons")
    if isinstance(reasons, str):
        reasons = [reasons]
    if isinstance(reasons, list) and any(str(r).lower() in _TRUNCATED_FINISH for r in reasons):
        signals.llm_truncated = True
    model = str(
        attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model") or ""
    ).strip()[:160]
    if not model:
        return
    provider = str(attrs.get("gen_ai.system") or attrs.get("gen_ai.provider.name") or "")
    when = start or _EPOCH
    if signals.llm_pick is None or when >= signals.llm_pick[0]:
        signals.llm_pick = (when, model, provider.strip().lower()[:80])


def _finish(signals: TurnSignals) -> None:
    # Manitos spans keep priority: the GenAI model fills in only when none of them named one, and
    # truncation is the lifecycle span's own flag whenever that span exists.
    if not signals.model and signals.llm_pick is not None:
        _, signals.model, signals.provider = signals.llm_pick
    if "manitos.turn.lifecycle" not in signals.seen_spans and signals.llm_truncated:
        signals.truncated = True
    signals.outcome, signals.outcome_source, signals.outcome_reason = derive_outcome(signals)
    signals.abstained_with_evidence = (
        signals.outcome == "abstained" and signals.retrieval_passages > 0
    )


_OUTCOME_METRICS = {f"outcome_{o}_rate": o for o in (*OUTCOMES, UNKNOWN)}


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
    if metric in _OUTCOME_METRICS:
        if not total:
            return None, 0
        wanted = _OUTCOME_METRICS[metric]
        return sum(1 for s in signals if s.outcome == wanted) / total, total
    if metric == "abstained_with_evidence_rate":
        abstained = [s for s in signals if s.outcome == "abstained"]
        if not abstained:
            return None, 0
        return sum(1 for s in abstained if s.abstained_with_evidence) / len(abstained), len(
            abstained
        )
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
