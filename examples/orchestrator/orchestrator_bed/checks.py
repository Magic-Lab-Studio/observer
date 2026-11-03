"""Checks: ask Observer's public API and compare with each episode's planted truth.

``api`` is any object with ``async get(path)`` and ``async post(path, json)`` returning an object
with ``status_code`` and ``json()`` (an ``httpx.AsyncClient`` works, so does the test client).
Each check returns ``(label, ok, detail)``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .emit import trace_id
from .model import Episode

Result = tuple[str, bool, str]


async def check_outcomes(api: Any, episodes: list[Episode], project: str, run: str) -> list[Result]:
    out: list[Result] = []
    data = (await api.get(f"/v1/agent/turns?project_id={project}&hours=2&limit=1000")).json()
    by_id = {row["trace_id"]: row for row in data.get("items", [])}
    for ep in episodes:
        row = by_id.get(trace_id(project, run, ep))
        if row is None:
            out.append((f"{project} {ep.key}: turn listed", False, "missing"))
            continue
        got = {k: row.get(k) for k in ep.truth}
        out.append((f"{project} {ep.key}: outcome", got == ep.truth, f"{got} != {ep.truth}"))
    abstained = next(e for e in episodes if e.key == "abstained_with_evidence")
    diag = (await api.get(f"/v1/traces/{trace_id(project, run, abstained)}/diagnostics")).json()
    out.append((f"{project}: abstention reason in diagnostics, no content",
                diag.get("outcome_reason") == "coverage_below_min"
                and "kb:0" not in str(diag), str({k: diag.get(k) for k in ("outcome",
                                                                          "outcome_reason")})))
    rule = {"name": f"bed {project} {run} abstained with evidence",
            "metric": "abstained_with_evidence_rate", "threshold": 0, "min_samples": 1,
            "window_minutes": 120, "project_id": project, "cooldown_minutes": 0}
    created = await api.post("/v1/alerts/rules", json=rule)
    await api.post("/v1/alerts/evaluate", json={})
    events = (await api.get(f"/v1/alerts/events?state=open&project_id={project}")).json()
    rule_id = created.json().get("id") if created.status_code == 200 else None
    fired = any(e.get("rule_id") == rule_id for e in events.get("events", events.get("items", [])))
    out.append((f"{project}: abstained-with-evidence alert opens", fired, str(events)[:200]))
    return out


CHECKS: dict[str, Callable[..., Awaitable[list[Result]]]] = {"10": check_outcomes}
