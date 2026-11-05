"""Bed episodes written through the real Python SDK helpers (``llm_observatory.agent``).

The spans are taken from the tracer in memory and returned as a ``/v1/traces/batch`` body, so the
same helpers an agent uses are checked end to end without a network exporter.
"""

from __future__ import annotations

from typing import Any


def to_sdk(project: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """``(batch body, {trace_id: truth})``."""
    from llm_observatory import (
        Tracer,
        agent,
        current_span,
        get_tracer,
        set_tracer,
        trace,
    )

    previous = get_tracer()
    tracer = Tracer(service_name=project)
    set_tracer(tracer)
    truths: dict[str, dict[str, Any]] = {}
    try:
        @trace(name="orchestrator.turn", span_type="agent")
        def abstains() -> str:
            agent.set_task("support_answer")
            with agent.retrieval("kb") as r:
                r.found(4, sources=["kb:1", "kb:2", "kb:3", "kb:4"])
            with agent.gate("coverage") as g:
                g.stop("coverage_below_min")
            agent.close("abstain")
            return current_span().trace_id

        @trace(name="orchestrator.turn", span_type="agent")
        def refuses() -> str:
            with agent.decision("policy", ["allow", "deny"]) as d:
                d.choose("deny", rejected={"allow": "policy_block"})
            agent.outcome("refused", "policy_block")
            return current_span().trace_id

        truths[abstains()] = {"outcome": "abstained", "outcome_source": "close",
                              "outcome_reason": "coverage_below_min",
                              "abstained_with_evidence": True}
        truths[refuses()] = {"outcome": "refused", "outcome_source": "attribute",
                             "outcome_reason": "policy_block", "abstained_with_evidence": False}
        body = {"spans": tracer.export()}
    finally:
        set_tracer(previous)
    return body, truths


async def check_sdk(api: Any, truths: dict[str, dict[str, Any]], project: str):
    data = (await api.get(f"/v1/agent/turns?project_id={project}&hours=2&limit=1000")).json()
    by_id = {row["trace_id"]: row for row in data.get("items", [])}
    out = []
    for tid, truth in truths.items():
        row = by_id.get(tid)
        got = {k: row.get(k) for k in truth} if row else None
        out.append((f"{project} {truth['outcome']} through the SDK helpers", got == truth,
                     f"{got} != {truth}"))
    return out
