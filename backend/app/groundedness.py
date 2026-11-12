"""Groundedness (roadmap step 9), ``rules`` mode: does the reply stand on what the trace fetched?

No model is called. The reply is the output of the trace's root span (or, without one, of its
last language-model call). The context is the output of the spans the project's configuration
names as evidence (tool and retrieval spans by default), capped per span and per trace. Each
sentence of the reply long enough to assert something is a *claim*, and a claim is supported
when:

* it cites a source the trace really fetched (a URL that appears in an evidence span, or an id in
  ``retrieval.sources``); a citation to anything else makes it unsupported;
* every number it states (two digits or more) appears in the context; one number the context does
  not contain makes it unsupported, and the whole reply ``ungrounded``: an invented figure is the
  clearest sign of an invented fact;
* or most of its content words (``OVERLAP``) appear in the context.

The share of supported claims is the score: ``grounded`` at or above the project's threshold,
``partial`` from half the threshold, ``ungrounded`` below. A trace without a reply or without
context (no evidence spans, or a capture mode that kept no content) is ``unknown`` with the reason,
never a pass; a trace outside ``applies_to`` is ``not_applicable``. Each unsupported claim is listed
in the result, short, as evidence.

``judge`` mode asks a language model (``app.evaluators.judge_backends``) which claims of the reply
the same bounded context does not support, as JSON; ``rules+judge`` runs both and keeps the worse.
A judge that fails or answers something invalid makes the result ``unknown``, never a pass. Privacy:
``off`` sends nothing; a project whose capture mode is ``metadata`` is ``unknown``
(``metadata_capture``) without calling the judge; a judge that is not on this machine (loopback)
receives the context only when the project sets ``judge.allow_remote``. A deterministic sample
(``sample_rate`` by trace id) and ``max_per_pass`` bound how many traces a pass sends to the judge.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capture import capture_mode
from app.case_check import LEVELS  # ("ungrounded", "partial", "grounded"), worst to best
from app.evaluators.judge_backends import (
    _DEFAULT_MODEL,
    JudgeConfig,
    JudgeUnavailableError,
    ask_judge,
)
from app.metrics_core import TurnSignals, aware, load_signals_for
from app.models.evaluation import Evaluation
from app.models.settings import ProjectConfig
from app.models.trace import Span, Trace

RUBRIC = "groundedness@1"
SECTION = "groundedness"
EVALUATOR_TYPE = "groundedness"  # its own type: turn-health readers take every "rule_based" row
MODES = ("off", "rules", "judge", "rules+judge")
BUILT_MODES = MODES
JUDGE_MODES = ("judge", "rules+judge")
JUDGE_REPLY_CHARS = 8000
OVERLAP = 0.6
MIN_CLAIM_WORDS = 4
MAX_LISTED = 10
CLAIM_CHARS = 160
SETTLE = timedelta(seconds=30)
_NAMESPACE = uuid.UUID("0b9b1f7e-6a0e-4c47-9a53-2f4d3c1e8a90")


class ContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    span_types: list[str] = Field(default_factory=lambda: ["tool", "retrieval"], max_length=10)
    name_patterns: list[str] = Field(default_factory=list, max_length=20)
    include_input: bool = False  # the root span's input (user message, system prompt)
    max_chars_per_span: int = Field(4000, ge=100, le=50_000)
    max_chars_per_trace: int = Field(20_000, ge=1000, le=200_000)


class JudgeSettings(BaseModel):
    """The project's judge; unset fields fall back to ``OBSERVATORY_JUDGE_*`` (keys always do)."""

    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai", "openai-compatible", "anthropic"] | None = None
    model: str | None = Field(None, max_length=200)
    base_url: str | None = Field(None, max_length=500)
    allow_remote: bool = False  # a judge off this machine receives the trace's context


class GroundednessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "rules", "judge", "rules+judge"] = "off"
    context: ContextConfig = Field(default_factory=ContextConfig)
    applies_to: list[str] = Field(default_factory=list, max_length=50)
    threshold: float = Field(0.8, gt=0, le=1)
    judge: JudgeSettings = Field(default_factory=JudgeSettings)
    sample_rate: float = Field(1.0, ge=0, le=1)  # share of traces the judge sees (by trace id)
    max_per_pass: int = Field(20, ge=1, le=500)  # traces sent to the judge per pass


def evaluation_id(trace_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{trace_id}:{RUBRIC}"))


async def load_config(session: AsyncSession, project_id: str | None) -> GroundednessConfig:
    """The project's groundedness section, or the default (``off``)."""
    if not project_id:
        return GroundednessConfig()
    row = await session.get(ProjectConfig, (project_id, SECTION))
    if row is None or not isinstance(row.value, dict):
        return GroundednessConfig()
    try:
        return GroundednessConfig.model_validate(row.value)
    except ValueError:
        return GroundednessConfig()  # a section stored by a newer schema: treated as off


# --- text --------------------------------------------------------------------------------------

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_BRACKET_REF = re.compile(r"\[([A-Za-z][\w.-]*:[\w.-]+|\d{1,3})\]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)|\n+")


def text_of(value: Any, limit: int = 1_000_000) -> str:
    """Every string inside a stored input/output (dicts and lists walked), joined by newlines."""
    parts: list[str] = []
    size = 0

    def walk(v: Any) -> None:
        nonlocal size
        if size >= limit:
            return
        if isinstance(v, str):
            parts.append(v)
            size += len(v) + 1
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            parts.append(str(v))
            size += len(str(v)) + 1

    walk(value)
    return "\n".join(parts)


def urls_in(text: str) -> list[str]:
    """URLs in ``text`` without the sentence punctuation that follows them."""
    return [u.rstrip(".,;:!?") for u in _URL.findall(text)]


def _canon(integer: str, fraction: str = "") -> str:
    integer = integer.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{integer}.{fraction}" if fraction else integer


def number_values(raw: str) -> set[str]:
    """The values a written number can mean, as canonical strings (``1492650.0`` and ``1.492.650``
    are both ``1492650``). The last separator is the decimal one when both kinds appear, or when
    it is followed by other than three digits; a repeated separator groups thousands. A single
    separator before exactly three digits (``1.492``, ``4,012``) is ambiguous: both readings."""
    seps = [c for c in raw if c in ".,"]
    if not seps:
        return {_canon(raw)}
    digits = re.split(r"[.,]", raw)
    if len(set(seps)) == 2:  # 1.492.650,5 / 1,492,650.5: the last one is the decimal separator
        return {_canon("".join(digits[:-1]), digits[-1])}
    if len(seps) > 1:  # 1.492.650 / 1,492,650: thousands groups
        return {_canon("".join(digits))}
    head, tail = digits
    decimal = _canon(head, tail)
    if len(tail) == 3 and 1 <= len(head) <= 3:
        return {decimal, _canon(head + tail)}
    return {decimal}


def numbers_in(text: str) -> set[str]:
    """Every value the numbers in ``text`` can mean."""
    return {v for n in _NUMBER.findall(text) for v in number_values(n)}


def _stated_numbers(text: str) -> list[str]:
    """Numbers a claim states, as written; one-digit numbers are not checked."""
    return [n for n in _NUMBER.findall(text) if sum(c.isdigit() for c in n) >= 2]


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)}


def split_claims(reply: str) -> list[str]:
    """Sentences that assert something: four words or more, not a question, code left out."""
    text = _CODE_BLOCK.sub(" ", reply)
    out = []
    for raw in _SENTENCE_END.split(text):
        sentence = raw.strip().lstrip("-*•0123456789.) ").strip()
        if not sentence or sentence.endswith("?"):
            continue
        if len(sentence.split()) >= MIN_CLAIM_WORDS:
            out.append(sentence)
    return out


def _citations(claim: str) -> tuple[list[str], str]:
    """``(cited sources, claim without the citations)``."""
    cited = [url for _, url in _MD_LINK.findall(claim)]
    rest = _MD_LINK.sub(lambda m: m.group(1), claim)
    cited += urls_in(rest)
    rest = _URL.sub(" ", rest)
    cited += _BRACKET_REF.findall(rest)
    rest = _BRACKET_REF.sub(" ", rest)
    return cited, rest


def _short(claim: str) -> str:
    return claim if len(claim) <= CLAIM_CHARS else claim[: CLAIM_CHARS - 1] + "…"


# --- scoring -----------------------------------------------------------------------------------


def score_reply(
    reply: str,
    context: str,
    *,
    fetched: set[str],
    sources: int,
    threshold: float = 0.8,
    given: str = "",
) -> dict[str, Any]:
    """Score one reply against its context (pure). ``fetched``: URLs and source ids the trace
    really read; ``sources``: how many evidence spans the context came from; ``given``: what the
    user said (the root span's input), whose numbers a reply may repeat without inventing them."""
    claims = split_claims(reply)
    if not claims:
        return {"status": "not_applicable", "reason": "no_claims", "score": None, "claims": 0}
    context_numbers = numbers_in(context) | numbers_in(given)
    context_words = _words(context)
    fetched_norm = {f.rstrip("/").lower() for f in fetched}
    supported = cited_ok = 0
    invented_number = False
    unsupported: list[dict[str, str]] = []  # why: unfetched_source | invented_number | no_support
    for claim in claims:
        cited, rest = _citations(claim)
        bad_cites = [c for c in cited if c.rstrip("/").lower() not in fetched_norm
                     and not c.isdigit()]
        numbers = _stated_numbers(rest)
        missing = [n for n in numbers if not number_values(n) & context_numbers]
        why = detail = ""
        if bad_cites:
            why, detail = "unfetched_source", bad_cites[0][:80]
        elif missing:
            why, detail = "invented_number", ", ".join(missing[:3])
            invented_number = True
        elif any(not c.isdigit() for c in cited):
            cited_ok += 1
        elif numbers:
            pass  # every number it states is in the sources
        else:
            words = _words(rest)
            share = len(words & context_words) / len(words) if words else 0.0
            if share < OVERLAP:
                why = "no_support"
        if why:
            unsupported.append({"claim": _short(claim), "why": why, "detail": detail})
        else:
            supported += 1
    score = supported / len(claims)
    if invented_number or score < threshold / 2:
        status = "ungrounded"
    elif score < threshold:
        status = "partial"
    else:
        status = "grounded"
    return {
        "status": status,
        "reason": "invented_number" if invented_number else "",
        "score": round(score, 4),
        "claims": len(claims),
        "supported": supported,
        "cited": cited_ok,
        "sources": sources,
        "unsupported": unsupported[:MAX_LISTED],
        "unsupported_count": len(unsupported),
    }


# --- judge -------------------------------------------------------------------------------------

JUDGE_SYSTEM = (
    "You check whether an assistant's reply is supported by the context it had: the output of the "
    "tools and searches it ran, and the user's message. Only the text under REPLY TO CHECK "
    "is checked: the user's message is never a claim. You do not use your own knowledge: a claim "
    "that is true but not in the context is unsupported. Questions, greetings and offers are not "
    "claims. "
    "Quote each unsupported claim from the reply. Answer with one JSON object and nothing else: "
    '{"claims": <number of factual claims in the reply>, '
    '"unsupported": [{"claim": "<the claim, short>", "why": "not_in_context" | "contradicted"}], '
    '"level": "grounded" | "partial" | "ungrounded"}. '
    "grounded: every claim is supported; partial: some are not; ungrounded: most are not, or one "
    "contradicts the context, or states a number the context does not contain."
)


def judge_config(settings: JudgeSettings) -> JudgeConfig:
    """``OBSERVATORY_JUDGE_*`` under the project's provider, model and URL (keys: env only)."""
    config = JudgeConfig.from_env()
    if settings.provider and settings.provider != config.provider:
        config.provider = settings.provider
        config.model = _DEFAULT_MODEL.get(settings.provider)  # not the env provider's model
    if settings.model:
        config.model = settings.model
    if settings.base_url:
        config.base_url = settings.base_url.strip()
    return config


def judge_is_local(config: JudgeConfig) -> bool:
    """True when the judge runs on this machine (an OpenAI-compatible endpoint on loopback)."""
    if config.provider != "openai-compatible" or not config.base_url:
        return False
    from urllib.parse import urlsplit

    host = (urlsplit(config.base_url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")


def sampled(trace_id: str, rate: float) -> bool:
    """A deterministic draw: the same trace is always in or out at a given rate."""
    if rate >= 1:
        return True
    if rate <= 0:
        return False
    draw = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return draw < rate


def parse_judge_answer(raw: str, threshold: float) -> dict[str, Any]:
    """The judge's JSON as a result, or ``ValueError`` when it is not the expected shape."""
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict) or data.get("level") not in LEVELS:
        raise ValueError("level missing or unknown")
    items = data.get("unsupported") or []
    if not isinstance(items, list):
        raise ValueError("unsupported is not a list")
    unsupported = []
    for item in items:
        claim = item.get("claim") if isinstance(item, dict) else item
        if not isinstance(claim, str) or not claim.strip():
            continue
        why = item.get("why") if isinstance(item, dict) else ""
        why = why if why in ("not_in_context", "contradicted") else "not_in_context"
        unsupported.append({"claim": _short(claim.strip()), "why": why, "detail": "judge"})
    claims = data.get("claims")
    claims = int(claims) if isinstance(claims, (int, float)) and claims >= 0 else 0
    claims = max(claims, len(unsupported))
    level = str(data["level"])
    if claims:
        score = (claims - len(unsupported)) / claims
    else:
        score = {"grounded": 1.0, "partial": threshold / 2, "ungrounded": 0.0}[level]
    return {
        "status": level,
        "reason": "",
        "score": round(score, 4),
        "claims": claims,
        "supported": claims - len(unsupported),
        "unsupported": unsupported[:MAX_LISTED],
        "unsupported_count": len(unsupported),
    }


async def judge_reply(
    settings: JudgeSettings, reply: str, context: str, given: str, threshold: float
) -> dict[str, Any]:
    """The judge's verdict on ``reply``; ``unknown`` with the reason when it gives none."""
    config = judge_config(settings)
    if not judge_is_local(config) and not settings.allow_remote:
        return {"status": "unknown", "reason": "judge_remote_not_allowed", "score": None}
    user = (
        f"CONTEXT (what the assistant fetched):\n{context}\n\n"
        f"USER MESSAGE:\n{given or '(none)'}\n\n"
        f"REPLY TO CHECK:\n{reply[:JUDGE_REPLY_CHARS]}"
    )
    try:
        raw = await ask_judge(config, JUDGE_SYSTEM, user)
    except JudgeUnavailableError as exc:
        return {"status": "unknown", "reason": f"judge_unavailable:{exc.reason}"[:120],
                "score": None}
    try:
        verdict = parse_judge_answer(raw, threshold)
    except (ValueError, TypeError):
        return {"status": "unknown", "reason": "judge_invalid", "score": None}
    verdict = _keep_claims_of_the_reply(verdict, reply)
    verdict["judge_model"] = str(config.model or "")[:160]
    return verdict


def _keep_claims_of_the_reply(verdict: dict[str, Any], reply: str) -> dict[str, Any]:
    """Drop "unsupported claims" the reply does not contain. A small local judge (llama3.2:3b, live
    run) listed the user's question «where is my refund» as an unsupported claim of a correct reply
    and called it ungrounded; with nothing left, the verdict is the judge's own claim count."""
    def tokens(text: str) -> set[str]:  # words and the numbers stated («180 USD» is a quote too)
        return _words(text) | set(_stated_numbers(text))

    reply_tokens = tokens(reply)
    kept, dropped = [], 0
    for item in verdict.get("unsupported") or []:
        claim = tokens(item["claim"])
        if claim and len(claim & reply_tokens) / len(claim) >= 0.75:  # a quote, not a paraphrase
            kept.append(item)
        else:
            dropped += 1
    if not dropped:
        return verdict
    claims = int(verdict.get("claims") or 0)
    verdict = {**verdict, "unsupported": kept, "unsupported_count": len(kept),
               "judge_claims_dropped": dropped}
    if not kept:
        return {**verdict, "status": "grounded", "score": 1.0, "supported": claims}
    if claims:
        verdict["supported"] = claims - len(kept)
        verdict["score"] = round((claims - len(kept)) / claims, 4)
    return verdict


def combine(rules: dict[str, Any], judge: dict[str, Any]) -> dict[str, Any]:
    """``rules+judge``: the worse of the two, both kept in the detail. An unknown judge makes the
    result unknown: a failed check is never a pass."""
    both = {"rules_result": rules, "judge_result": judge}
    if judge.get("status") not in LEVELS:
        return {**rules, **both, "status": "unknown", "reason": judge.get("reason") or "judge",
                "score": None}
    if rules.get("status") not in LEVELS:
        return {**judge, **both}
    worse = rules if LEVELS.index(rules["status"]) <= LEVELS.index(judge["status"]) else judge
    scores = [r["score"] for r in (rules, judge) if isinstance(r.get("score"), (int, float))]
    unsupported = (rules.get("unsupported") or []) + (judge.get("unsupported") or [])
    return {**worse, **both, "score": min(scores) if scores else None,
            "unsupported": unsupported[:MAX_LISTED]}


def _matches_context(span: Any, cfg: ContextConfig) -> bool:
    if str(span.span_type or "") in cfg.span_types:
        return True
    return any(fnmatch.fnmatchcase(str(span.name or ""), p) for p in cfg.name_patterns)


def _applies(cfg: GroundednessConfig, signals: TurnSignals | None, tags: list[str]) -> bool:
    if not cfg.applies_to:
        return True
    have = {t for t in (signals.intent if signals else "", signals.task if signals else "") if t}
    return bool(set(cfg.applies_to) & (have | set(tags)))


def _end_key(span: Any) -> datetime:
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return aware(span.end_time) or aware(span.start_time) or floor


async def evaluate_trace(
    session: AsyncSession,
    trace: Trace,
    cfg: GroundednessConfig,
    signals: TurnSignals | None = None,
) -> dict[str, Any]:
    """The groundedness result of one stored trace under ``cfg`` (reads its spans' content)."""
    base: dict[str, Any] = {"rubric": RUBRIC, "mode": cfg.mode}
    if signals is not None:
        base["model"] = (signals.answered_by or signals.model)[:160]
        base["provider"] = signals.provider
    meta = trace.meta if isinstance(trace.meta, dict) else {}
    tags = [str(t) for t in meta.get("tags", []) if isinstance(t, str)] if isinstance(
        meta.get("tags"), list) else []
    if not _applies(cfg, signals, tags):
        return {**base, "status": "not_applicable", "reason": "outside_applies_to", "score": None}
    uses_judge = cfg.mode in JUDGE_MODES
    if uses_judge and await capture_mode(session, trace.project_id, "full") == "metadata":
        # nothing of the content was kept, and nothing is sent anywhere
        return {**base, "status": "unknown", "reason": "metadata_capture", "score": None}
    if cfg.mode == "judge" and not sampled(str(trace.id), cfg.sample_rate):
        return {**base, "status": "not_applicable", "reason": "not_sampled", "score": None}
    spans = list(
        (await session.execute(select(Span).where(Span.trace_id == trace.id))).scalars()
    )
    root = next((s for s in spans if s.parent_span_id is None and s.out), None)
    reply = text_of(root.out) if root is not None else ""
    if not reply.strip():
        llms = sorted((s for s in spans if s.span_type == "llm" and s.out), key=_end_key)
        reply = text_of(llms[-1].out) if llms else ""
    if not reply.strip():
        return {**base, "status": "unknown", "reason": "no_reply_text", "score": None}
    ctx = cfg.context
    parts: list[str] = []
    fetched: set[str] = set()
    used = dropped = sources = 0
    evidence = [s for s in spans if s is not root and _matches_context(s, ctx)]
    for span in sorted(evidence, key=_end_key):
        attrs = span.attributes if isinstance(span.attributes, dict) else {}
        for source in attrs.get("retrieval.sources") or []:
            fetched.add(str(source))
        text = text_of(span.out)
        fetched.update(urls_in(text_of(span.inp)))
        if not text.strip():
            continue
        sources += 1
        fetched.update(urls_in(text))
        take = text[: ctx.max_chars_per_span]
        room = ctx.max_chars_per_trace - used
        if room <= 0:
            dropped += len(text)
            continue
        take = take[:room]
        dropped += len(text) - len(take)
        used += len(take)
        parts.append(take)
    if ctx.include_input and root is not None and root.inp:
        parts.append(text_of(root.inp)[: ctx.max_chars_per_span])
    context = "\n".join(parts)
    if not context.strip():
        return {**base, "status": "unknown", "reason": "no_context", "score": None,
                "sources": 0}
    top = next((s for s in spans if s.parent_span_id is None and s.inp), None)
    given = text_of(top.inp)[: ctx.max_chars_per_span] if top is not None else ""
    extra = {"context_chars": used, "dropped_chars": dropped}
    rules = None
    if cfg.mode in ("rules", "rules+judge"):
        rules = score_reply(reply, context, fetched=fetched, sources=sources,
                            threshold=cfg.threshold, given=given)
    skip_judge = cfg.mode == "rules+judge" and not sampled(str(trace.id), cfg.sample_rate)
    if not uses_judge or skip_judge:
        return {**base, **(rules or {}), **extra}
    judged = await judge_reply(cfg.judge, reply, context, given, cfg.threshold)
    if rules is None:
        return {**base, **judged, "sources": sources, **extra}
    if rules.get("status") == "not_applicable":
        return {**base, **rules, **extra}  # nothing to judge: the reply asserts nothing
    return {**base, **combine(rules, judged), "sources": sources, **extra}


async def evaluate_project(
    session: AsyncSession,
    project_id: str,
    *,
    trace_ids: list[str] | None = None,
    force: bool = False,
    days: float = 7.0,
    batch: int = 200,
    now: datetime | None = None,
) -> list[Evaluation]:
    """Score ended traces of a project that have no groundedness evaluation yet (or the given
    ``trace_ids``; ``force`` re-scores them). Nothing happens unless the project's mode runs
    rules. Flushes, never commits."""
    cfg = await load_config(session, project_id)
    if cfg.mode == "off":
        return []
    if cfg.mode in JUDGE_MODES:
        batch = min(batch, cfg.max_per_pass)  # each trace may be a judge call
    now = now or datetime.now(timezone.utc)
    query = select(Trace).where(Trace.project_id == project_id)
    if trace_ids is not None:
        query = query.where(Trace.id.in_(trace_ids[:batch]))
    else:
        query = (
            query.where(
                Trace.created_at >= now - timedelta(days=days),
                Trace.end_time.is_not(None),
                Trace.end_time <= now - SETTLE,
            )
            .order_by(Trace.created_at.desc())
            .limit(batch * 4)
        )
    traces = list((await session.execute(query)).scalars())
    by_id = {evaluation_id(str(t.id)): t for t in traces}
    existing = {
        row.id: row
        for row in (
            await session.execute(select(Evaluation).where(Evaluation.id.in_(list(by_id))))
        ).scalars()
    }
    todo = [t for eid, t in by_id.items() if force or eid not in existing][:batch]
    if not todo:
        return []
    signals = {s.trace_id: s for s in await load_signals_for(session, [str(t.id) for t in todo])}
    out: list[Evaluation] = []
    for trace in todo:
        result = await evaluate_trace(session, trace, cfg, signals.get(str(trace.id)))
        eid = evaluation_id(str(trace.id))
        row = existing.get(eid)
        if row is None:
            row = Evaluation(id=eid, trace_id=str(trace.id), evaluator_type=EVALUATOR_TYPE)
            session.add(row)
        row.score = result.get("score")
        row.criteria = {"rubric": RUBRIC, "criteria": ["groundedness"]}
        row.result = result
        out.append(row)
    await session.flush()
    return out


async def configured_projects(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        select(ProjectConfig.project_id, ProjectConfig.value).where(
            ProjectConfig.section == SECTION
        )
    )
    return [
        pid for pid, value in rows.all()
        if isinstance(value, dict) and value.get("mode") in ("rules", "judge", "rules+judge")
    ]


async def ungrounded_rate(
    session: AsyncSession,
    *,
    project_id: str | None,
    environment: str | None,
    since: datetime,
) -> tuple[float | None, int]:
    """Share of scored replies (grounded, partial or ungrounded) that came out ``ungrounded``."""
    query = (
        select(Evaluation.result)
        .join(Trace, Trace.id == Evaluation.trace_id)
        .where(Evaluation.evaluator_type == EVALUATOR_TYPE, Trace.created_at >= since)
        .limit(20000)
    )
    if project_id:
        query = query.where(Trace.project_id == project_id)
    if environment:
        query = query.where(Trace.environment == environment)
    statuses = [
        r.get("status")
        for (r,) in (await session.execute(query)).all()
        if isinstance(r, dict) and r.get("rubric") == RUBRIC and r.get("status") in LEVELS
    ]
    if not statuses:
        return None, 0
    return statuses.count("ungrounded") / len(statuses), len(statuses)
