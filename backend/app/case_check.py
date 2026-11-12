"""Check a test case's expectations against what a trace shows (pure, no I/O).

Each check is ``pass``, ``fail`` or ``unknown``. ``unknown`` means the data to decide is not there
(never silently a pass): tool expectations need tool spans, and text phrases need the reply text,
which exists only when the project's capture mode stores content (Manitos exports metadata only).
"""

from __future__ import annotations

import re
from typing import Any

LEVELS = ("ungrounded", "partial", "grounded")  # groundedness levels, worst to best


def _check(kind: str, name: str, status: str, detail: str = "") -> dict[str, str]:
    return {"kind": kind, "name": name, "status": status, "detail": detail}


def _plain(text: str) -> str:
    """Lower case with every run of whitespace (narrow and non-breaking spaces too) as one space."""
    return re.sub(r"\s+", " ", text).casefold().strip()


def check_case(
    expect: dict[str, Any], observed: dict[str, Any], reply: str | None = None
) -> dict[str, Any]:
    """``{status, checks, unverified}`` for one case against one trace's observed metadata.
    ``reply``: the trace's reply text, when its capture mode stored it; it is only read here, never
    kept in the case."""
    checks: list[dict[str, str]] = []
    health = expect.get("health") or {}
    failed_run = observed.get("status") == "error" or observed.get("tool_error") is True
    flags = (
        ("no_error", failed_run, "error or tool error"),
        ("no_truncation", observed.get("truncated") is True, "truncated"),
        ("no_degradation", observed.get("degraded") is True, "degraded"),
        ("no_fallback", observed.get("fallback") is True, "used the fallback route"),
    )
    for name, bad, why in flags:
        if health.get(name):
            checks.append(_check("health", name, "fail" if bad else "pass", why if bad else ""))
    limit = health.get("max_duration_ms")
    if limit:
        took = observed.get("duration_ms")
        if took is None:
            checks.append(_check("health", "max_duration_ms", "unknown", "no duration"))
        elif took > limit:
            checks.append(_check("health", "max_duration_ms", "fail", f"{round(took)} ms"))
        else:
            checks.append(_check("health", "max_duration_ms", "pass", f"{round(took)} ms"))

    tools = expect.get("tools") or {}
    seen = set(observed.get("tools") or [])
    for name in tools.get("required") or []:
        if not seen:
            checks.append(_check("tools", f"required:{name}", "unknown", "no tool spans"))
        else:
            checks.append(_check("tools", f"required:{name}", "pass" if name in seen else "fail"))
    for name in tools.get("forbidden") or []:
        if not seen:
            checks.append(_check("tools", f"forbidden:{name}", "unknown", "no tool spans"))
        else:
            checks.append(_check("tools", f"forbidden:{name}", "fail" if name in seen else "pass"))

    text = expect.get("text") or {}
    plain = _plain(reply) if reply and reply.strip() else None
    for key in ("must_contain", "must_not_contain"):
        for phrase in text.get(key) or []:
            if plain is None:
                checks.append(_check("text", f"{key}:{phrase}", "unknown", "no reply text stored"))
                continue
            found = _plain(phrase) in plain
            ok = found if key == "must_contain" else not found
            checks.append(_check("text", f"{key}:{phrase}", "pass" if ok else "fail",
                                 "" if ok else ("not in the reply" if key == "must_contain" else
                                                "in the reply")))

    minimum = (expect.get("groundedness") or {}).get("min_level")
    if minimum:
        g = observed.get("groundedness") or {}
        status = g.get("status")
        name = f"groundedness>={minimum}"
        if status in LEVELS:
            ok = LEVELS.index(status) >= LEVELS.index(minimum)
            detail = f"{status} ({g.get('score')})"
            checks.append(_check("groundedness", name, "pass" if ok else "fail", detail))
        else:
            checks.append(_check("groundedness", name, "unknown", g.get("reason") or "not scored"))

    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        status = "fail"
    elif "pass" in statuses:
        status = "pass"
    else:
        status = "unknown"
    return {"status": status, "checks": checks, "unverified": statuses.count("unknown")}


def binary_entropy(passes: int, fails: int) -> float:
    """How split the decided repetitions are: 0 if they agree, 1 at an even split (3/5: 0.971)."""
    decided = passes + fails
    if not passes or not fails:
        return 0.0
    from math import log2

    p = passes / decided
    return round(-(p * log2(p) + (1 - p) * log2(1 - p)), 3)


def aggregate_repetitions(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One entry per case from its results (``{item_id, status, health_score, duration_ms}``).

    A case whose decided repetitions both passed and failed is ``unstable`` (step 12): the same
    input gave different outcomes. Otherwise its status is the decided one (``pass``/``fail``), or
    ``unknown`` when no repetition could be checked. Health and duration are means. A case run
    once keeps exactly its result.
    """
    by_case: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_case.setdefault(result["item_id"], []).append(result)
    out: dict[str, dict[str, Any]] = {}
    for item_id, rows in by_case.items():
        dist = {"pass": 0, "fail": 0, "unknown": 0}
        for row in rows:
            dist[row["status"]] = dist.get(row["status"], 0) + 1
        if dist["pass"] and dist["fail"]:
            status = "unstable"
        elif dist["pass"]:
            status = "pass"
        elif dist["fail"]:
            status = "fail"
        else:
            status = "unknown"

        def mean(key: str, rows: list[dict[str, Any]] = rows) -> float | None:
            values = [r[key] for r in rows if r.get(key) is not None]
            return sum(values) / len(values) if values else None

        out[item_id] = {
            "item_id": item_id,
            "status": status,
            "repetitions": len(rows),
            "distribution": dist,
            "entropy": binary_entropy(dist["pass"], dist["fail"]),
            "health_score": mean("health_score"),
            "duration_ms": mean("duration_ms"),
        }
    return out


def compare_results(
    base: dict[str, dict[str, Any]], candidate: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Per-case difference between two runs (``item_id -> {status, health_score, duration_ms}``).

    ``regressed``: pass -> fail. ``improved``: fail -> pass. ``unstable``: either side gave
    different outcomes over its repetitions (reported apart, not a regression). Cases with an
    unknown side or missing from one run are reported as such, never counted as a change.
    """
    cases = []
    counts = {"regressed": 0, "improved": 0, "same": 0, "unstable": 0, "unknown": 0, "missing": 0}
    for item_id in sorted(set(base) | set(candidate)):
        a, b = base.get(item_id), candidate.get(item_id)
        if a is None or b is None:
            change = "missing"
        elif "unstable" in (a["status"], b["status"]):
            change = "unstable"
        elif a["status"] == "pass" and b["status"] == "fail":
            change = "regressed"
        elif a["status"] == "fail" and b["status"] == "pass":
            change = "improved"
        elif "unknown" in (a["status"], b["status"]) and a["status"] != b["status"]:
            change = "unknown"
        else:
            change = "same"
        counts[change] += 1

        def delta(key: str, a=a, b=b) -> float | None:
            if a is None or b is None or a.get(key) is None or b.get(key) is None:
                return None
            return round(b[key] - a[key], 4)

        cases.append(
            {
                "item_id": item_id,
                "change": change,
                "base": a and {"status": a["status"], "health_score": a.get("health_score")},
                "candidate": b and {"status": b["status"], "health_score": b.get("health_score")},
                "health_delta": delta("health_score"),
                "duration_delta_ms": delta("duration_ms"),
            }
        )
    return {"summary": counts, "cases": cases}
