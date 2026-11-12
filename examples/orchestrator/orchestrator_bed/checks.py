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


async def check_structure(api: Any, episodes: list[Episode], project: str,
                          run: str) -> list[Result]:
    """Step 11: whatever the format, every step hangs from its episode's root, and a field the
    sender's mapping did not name (``customer_note``) is nowhere in what Observer stored."""
    from .events_emit import UNMAPPED_MARKER

    out: list[Result] = []
    for ep in episodes:
        spans = (await api.get(f"/v1/traces/{trace_id(project, run, ep)}/spans")).json()
        if not isinstance(spans, list) or len(spans) != len(ep.spans):
            out.append((f"{project} {ep.key}: all spans stored", False,
                        f"{len(spans) if isinstance(spans, list) else spans} != {len(ep.spans)}"))
            continue
        roots = [s for s in spans if s["parent_id"] is None]
        children_ok = len(roots) == 1 and all(s["parent_id"] == roots[0]["id"]
                                              for s in spans if s is not roots[0])
        out.append((f"{project} {ep.key}: steps hang from the root", children_ok,
                    str([(s["name"], s["parent_id"]) for s in spans])[:200]))
        out.append((f"{project} {ep.key}: unmapped field not stored",
                    UNMAPPED_MARKER not in str(spans) and "customer_note" not in str(spans), ""))
    return out


async def check_groundedness(api: Any, episodes: list[Episode], project: str,
                             run: str) -> list[Result]:
    """Step 9, rules mode: with the replies stored (batch), the invented refund is ungrounded with
    that number listed and its twin is grounded; formats that carry no content stay unknown."""
    from .model import GROUNDEDNESS_TRUTH

    out: list[Result] = []
    planted = {trace_id(project, run, e): e.key for e in episodes if e.key in GROUNDEDNESS_TRUTH}
    put = await api.put(f"/v1/projects/{project}/config/groundedness", json={"mode": "rules"})
    out.append((f"{project}: groundedness switched to rules", put.status_code == 200,
                put.text[:200]))
    r = await api.post(f"/v1/projects/{project}/groundedness/evaluate",
                       json={"trace_ids": list(planted), "force": True})
    results = {row["trace_id"]: row for row in r.json().get("results", [])} if (
        r.status_code == 200) else {}
    for tid, key in planted.items():
        got = results.get(tid, {})
        if project == "bed-batch":
            status, number = GROUNDEDNESS_TRUTH[key]
            listed = [u.get("detail") for u in got.get("unsupported", [])]
            ok = got.get("status") == status and (number is None or number in listed)
            out.append((f"{project} {key}: groundedness {status}", ok,
                        f"{got.get('status')} {listed}"))
        else:
            out.append((f"{project} {key}: no content stored, groundedness unknown",
                        got.get("status") == "unknown", str(got.get("status"))))
    return out


async def check_funnel(api: Any, episodes: list[Episode], project: str, run: str) -> list[Result]:
    """Step 14: the gate funnel counts exactly the gate stops the episodes planted, with their
    reason codes, and lists the gate that stopped most turns first."""
    from collections import Counter

    stops: Counter[str] = Counter()
    seen: Counter[str] = Counter()
    codes: dict[str, Counter[str]] = {}
    for ep in episodes:
        for span in ep.spans:
            if span.span_type != "gate":
                continue
            name = span.attrs.get("gate.name", "")
            seen[name] += 1
            if span.attrs.get("gate.status") == "stop":
                stops[name] += 1
                codes.setdefault(name, Counter()).update(span.attrs.get("gate.reason_codes") or [])
    data = (await api.get(f"/v1/agent/funnel?project_id={project}&hours=2")).json()
    got = {g["gate"]: g for g in data.get("gates", [])}
    out: list[Result] = []
    for name in seen:
        g = got.get(name, {})
        want_codes = {c: n for c, n in codes.get(name, Counter()).items()}
        got_codes = {r["code"]: r["count"] for r in g.get("reason_codes", [])}
        ok = g.get("turns") == seen[name] and g.get("stopped") == stops[name] and (
            got_codes == want_codes)
        out.append((f"{project} gate {name}: {stops[name]} of {seen[name]} stopped", ok,
                    str({k: g.get(k) for k in ("turns", "stopped", "reason_codes")})))
    if stops:
        top = max(stops, key=lambda n: stops[n])
        first = (data.get("gates") or [{}])[0].get("gate")
        out.append((f"{project}: the gate that stops most comes first ({top})",
                    stops[first] == stops[top] if first in stops else False, str(first)))
    return out


CHECKS: dict[str, Callable[..., Awaitable[list[Result]]]] = {"9": check_groundedness,
                                                              "10": check_outcomes,
                                                              "11": check_structure,
                                                              "14": check_funnel}
