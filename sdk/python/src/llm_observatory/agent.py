"""Helpers for the agent span contract ``observer.agent.v1`` (``docs/agent-spans.md``).

Tell Observer how a turn ended and why, with metadata only (names, codes, counts):

    from llm_observatory import agent

    @trace(name="turn", span_type="agent")
    def answer(question):
        agent.set_task("support_answer")
        with agent.retrieval("kb") as r:
            passages = search(question)
            r.found(len(passages), sources=[p.id for p in passages])
        with agent.gate("coverage") as g:
            if len(passages) < 2:
                g.stop("coverage_below_min")
                agent.close("abstain")
                return None
        ...
        agent.outcome("answered")

Each helper opens a child span of the current one with the contract's ``span_type`` and keys, and
makes it the current span while the block runs. An exception inside the block marks the span as
an error and propagates unchanged. Content (arguments, passages, replies) does not belong here:
put it in a span's input and output, where the project's capture mode applies.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from llm_observatory.tracer import Span, SpanStatus, _current_span, _record_error, get_tracer

OUTCOMES = ("answered", "degraded", "abstained", "clarification", "refused", "failed")
CLOSE_KINDS = ("answer", "clarify", "abstain", "refuse", "fail")
GATE_STATUSES = ("pass", "stop", "skip")


def _codes(values: Iterable[Any]) -> list[str]:
    return [str(v)[:80] for v in values if str(v).strip()][:16]


def _target(span: Span | None) -> Span:
    target = span or _current_span.get()
    if target is None:
        raise RuntimeError("no current span: call inside a @trace function or pass span=")
    return target


def outcome(kind: str, reason: str | None = None, *, span: Span | None = None) -> None:
    """Declare how the turn ended (``observer.outcome``) on ``span`` or the current span.

    Declare it on the turn's root span when you can: the root's value wins over a child's."""
    if kind not in OUTCOMES:
        raise ValueError(f"unknown outcome {kind!r}; one of {', '.join(OUTCOMES)}")
    target = _target(span)
    target.set_attribute("observer.outcome", kind)
    if reason:
        target.set_attribute("observer.outcome.reason", str(reason)[:160])


def set_task(task: str, *, span: Span | None = None) -> None:
    """The kind of task the turn performs (``observer.task``), for grouping in Explore."""
    _target(span).set_attribute("observer.task", str(task)[:80])


def set_user(user_id: str, *, span: Span | None = None) -> None:
    """An opaque id of the user the turn served (``user.id``); never an email or a name."""
    _target(span).set_attribute("user.id", str(user_id)[:128])


@contextmanager
def _agent_span(name: str, span_type: str, attrs: dict[str, Any]) -> Iterator[Span]:
    span = get_tracer().start_span(name, span_type=span_type)
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)
    token = _current_span.set(span)
    try:
        yield span
        if span.status != SpanStatus.ERROR:  # a tool error recorded by the block stays an error
            span.set_status(SpanStatus.OK)
    except BaseException as exc:
        _record_error(span, exc)
        raise
    finally:
        span.end()
        _current_span.reset(token)


class Gate:
    """A check the turn must pass. ``stop()`` records why it did not; a block that ends without a
    call and without an error passed."""

    def __init__(self, span: Span) -> None:
        self.span = span
        self.status: str | None = None

    def passed(self) -> None:
        self._set("pass", ())

    def stop(self, *reason_codes: str) -> None:
        self._set("stop", reason_codes)

    def skip(self, *reason_codes: str) -> None:
        self._set("skip", reason_codes)

    def _set(self, status: str, reason_codes: Iterable[str]) -> None:
        self.status = status
        self.span.set_attribute("gate.status", status)
        codes = _codes(reason_codes)
        if codes:
            self.span.set_attribute("gate.reason_codes", codes)


@contextmanager
def gate(name: str, *, stage: str | None = None, requirement: str | None = None) -> Iterator[Gate]:
    attrs = {"gate.name": name, "gate.stage": stage or name, "gate.requirement": requirement}
    with _agent_span(f"gate.{name}", "gate", attrs) as span:
        g = Gate(span)
        yield g
        if g.status is None:
            g.passed()


class Decision:
    def __init__(self, span: Span) -> None:
        self.span = span

    def choose(
        self,
        choice: str,
        *,
        rejected: dict[str, str] | None = None,
        confidence: float | None = None,
        final_by: str | None = None,
    ) -> None:
        """The option taken; ``rejected`` maps each discarded option to its reason code."""
        self.span.set_attribute("decision.choice", str(choice)[:120])
        if rejected:
            self.span.set_attribute(
                "decision.rejected", [f"{k}:{v}"[:120] for k, v in rejected.items()][:16]
            )
        if confidence is not None:
            self.span.set_attribute("decision.confidence", float(confidence))
        if final_by:
            self.span.set_attribute("decision.final_by", str(final_by)[:80])


@contextmanager
def decision(kind: str, options: Iterable[str] = ()) -> Iterator[Decision]:
    listed = [str(o)[:120] for o in options][:16]
    attrs = {"decision.kind": kind, "decision.options": listed or None}
    with _agent_span(f"decision.{kind}", "decision", attrs) as span:
        yield Decision(span)


class Retrieval:
    def __init__(self, span: Span) -> None:
        self.span = span

    def found(
        self,
        passages: int,
        *,
        sources: Iterable[str] = (),
        top_score: float | None = None,
    ) -> None:
        """How many passages came back and the ids of their sources (ids, not URLs)."""
        self.span.set_attribute("retrieval.passages", int(passages))
        ids = [str(s)[:120] for s in sources][:32]
        if ids:
            self.span.set_attribute("retrieval.sources", ids)
        if top_score is not None:
            self.span.set_attribute("retrieval.top_score", float(top_score))


@contextmanager
def retrieval(name: str, *, index: str | None = None) -> Iterator[Retrieval]:
    with _agent_span(f"retrieval.{name}", "retrieval",
                     {"retrieval.name": name, "retrieval.index": index}) as span:
        r = Retrieval(span)
        yield r
        if "retrieval.passages" not in span.attributes:
            r.found(0)


class ToolCall:
    def __init__(self, span: Span) -> None:
        self.span = span

    def result(self, *, size: int | None = None, error_code: str | None = None) -> None:
        if size is not None:
            self.span.set_attribute("tool.result_size", int(size))
        if error_code:
            self.span.set_attribute("tool.error_code", str(error_code)[:80])
            self.span.set_status(SpanStatus.ERROR)


@contextmanager
def tool_call(name: str, arg_names: Iterable[str] = ()) -> Iterator[ToolCall]:
    """A tool call; ``arg_names`` are the argument *names* only (values are content)."""
    attrs = {"tool.name": name, "tool.arg_names": [str(a)[:60] for a in arg_names][:16] or None}
    with _agent_span(name, "tool", attrs) as span:
        yield ToolCall(span)


class Verification:
    def __init__(self, span: Span) -> None:
        self.span = span

    def result(
        self, accepted: int, rejected: int = 0, pruned: int = 0, reason_codes: Iterable[str] = ()
    ) -> None:
        self.span.set_attribute("verification.claims_accepted", int(accepted))
        self.span.set_attribute("verification.claims_rejected", int(rejected))
        self.span.set_attribute("verification.claims_pruned", int(pruned))
        codes = _codes(reason_codes)
        if codes:
            self.span.set_attribute("verification.reason_codes", codes)


@contextmanager
def verification(name: str = "claims") -> Iterator[Verification]:
    with _agent_span(f"verification.{name}", "verification", {}) as span:
        yield Verification(span)


@contextmanager
def replan(reason: str, *, attempt: int | None = None) -> Iterator[Span]:
    with _agent_span("replan", "replan", {"replan.reason": reason, "replan.attempt": attempt}) as s:
        yield s


def close(kind: str, reason_code: str | None = None) -> None:
    """End the turn's reasoning: answer, clarify, abstain, refuse or fail (an instant span)."""
    if kind not in CLOSE_KINDS:
        raise ValueError(f"unknown close kind {kind!r}; one of {', '.join(CLOSE_KINDS)}")
    attrs = {"close.kind": kind, "close.reason_code": reason_code}
    with _agent_span("close", "close", attrs):
        pass
