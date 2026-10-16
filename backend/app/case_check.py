"""Check a test case's expectations against what a trace shows (pure, no I/O).

Each check is ``pass``, ``fail`` or ``unknown``. ``unknown`` means the data to decide is not there
(never silently a pass): Manitos does not export tool-call spans or reply text yet, so tool and
text expectations stay unknown until it does.
"""

from __future__ import annotations

from typing import Any


def _check(kind: str, name: str, status: str, detail: str = "") -> dict[str, str]:
    return {"kind": kind, "name": name, "status": status, "detail": detail}


def check_case(expect: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    """``{status, checks, unverified}`` for one case against one trace's observed metadata."""
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
    for key in ("must_contain", "must_not_contain"):
        for phrase in text.get(key) or []:
            checks.append(_check("text", f"{key}:{phrase}", "unknown", "reply text not exported"))

    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        status = "fail"
    elif "pass" in statuses:
        status = "pass"
    else:
        status = "unknown"
    return {"status": status, "checks": checks, "unverified": statuses.count("unknown")}


def compare_results(
    base: dict[str, dict[str, Any]], candidate: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Per-case difference between two runs (``item_id -> {status, health_score, duration_ms}``).

    ``regressed``: pass -> fail. ``improved``: fail -> pass. Cases with an unknown side or missing
    from one run are reported as such, never counted as a change.
    """
    cases = []
    counts = {"regressed": 0, "improved": 0, "same": 0, "unknown": 0, "missing": 0}
    for item_id in sorted(set(base) | set(candidate)):
        a, b = base.get(item_id), candidate.get(item_id)
        if a is None or b is None:
            change = "missing"
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
