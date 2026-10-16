"""Regression gate for CI: decide from stored runs whether a version may ship.

Observer does not execute the agent. The pipeline runs the dataset's cases, then calls this command
with the traces it produced (``--results``) or with a run it already recorded. The gate reads the
run and, optionally, a baseline run of the same dataset from the Observer API, and exits:

* ``0`` the candidate meets every threshold;
* ``1`` a regression or a threshold failure (the reasons are printed);
* ``2`` it could not decide (bad arguments, Observer unreachable, unknown dataset or run).

A gate must never pass by verifying nothing, so a candidate with no decided case fails unless
``--allow-unverified`` is given, and ``unknown`` cases are reported apart from passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPORT_SCHEMA = "observer.regression.v1"
DEFAULT_URL = "http://127.0.0.1:8100"


class GateError(Exception):
    """The gate could not reach a verdict (exit code 2)."""


@dataclass
class Thresholds:
    max_regressions: int = 0
    max_failures: int | None = None  # None: 0 without a baseline, unlimited with one
    min_pass_rate: float | None = None
    max_health_drop: float | None = None
    fail_on_unknown: bool = False
    allow_unverified: bool = False


@dataclass
class Verdict:
    passed: bool
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def evaluate(
    candidate: dict[str, Any], comparison: dict[str, Any] | None, limits: Thresholds
) -> Verdict:
    """Pure decision over a run summary and (optionally) its comparison with a baseline."""
    failures: list[str] = []
    notes: list[str] = []
    summary = candidate["summary"]
    decided = summary["pass"] + summary["fail"]

    if decided == 0 and not limits.allow_unverified:
        failures.append("no case could be verified (all unknown); nothing proves this version")
    if summary["unknown"]:
        message = f"{summary['unknown']} of {summary['cases']} cases are unknown (not verifiable)"
        (failures if limits.fail_on_unknown else notes).append(message)

    max_failures = limits.max_failures
    if max_failures is None and comparison is None:
        max_failures = 0
    if max_failures is not None and summary["fail"] > max_failures:
        failures.append(f"{summary['fail']} failed cases (allowed {max_failures})")

    rate = summary.get("pass_rate")
    if limits.min_pass_rate is not None and rate is not None and rate < limits.min_pass_rate:
        failures.append(f"pass rate {rate:.2f} is below {limits.min_pass_rate:.2f}")

    if comparison is not None:
        regressed = comparison["summary"]["regressed"]
        if regressed > limits.max_regressions:
            names = [c["item_id"][:8] for c in comparison["cases"] if c["change"] == "regressed"]
            failures.append(
                f"{regressed} cases regressed against the baseline (allowed "
                f"{limits.max_regressions}): {', '.join(names[:10])}"
            )
        base_health = comparison["base"]["summary"].get("avg_health")
        cand_health = comparison["candidate"]["summary"].get("avg_health")
        if limits.max_health_drop is not None and None not in (base_health, cand_health):
            drop = base_health - cand_health
            if drop > limits.max_health_drop:
                failures.append(
                    f"average health fell {drop:.2f} (allowed {limits.max_health_drop:.2f})"
                )
        notes.append(f"{comparison['summary']['improved']} cases improved")
    return Verdict(passed=not failures, failures=failures, notes=notes)


@dataclass
class Config:
    url: str = DEFAULT_URL
    api_key: str = ""
    dataset: str = ""
    candidate: str = "latest"
    baseline: str = ""
    results_path: str = ""
    name: str = ""
    commit: str = ""
    model: str = ""
    config: dict[str, str] = field(default_factory=dict)
    report_path: str = ""
    limits: Thresholds = field(default_factory=Thresholds)
    timeout: float = 30.0


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> Any:
    try:
        response = await client.get(path, params=params or None)
    except httpx.HTTPError as exc:
        raise GateError(f"Observer unreachable: {exc.__class__.__name__}") from exc
    if response.status_code != 200:
        raise GateError(f"GET {path} answered {response.status_code}")
    return response.json()


async def _resolve_dataset(client: httpx.AsyncClient, ref: str) -> dict[str, Any]:
    matches = [d for d in await _get(client, "/v1/datasets") if ref in (d["id"], d["name"])]
    if not matches:
        raise GateError(f"dataset not found: {ref}")
    if len(matches) > 1:
        raise GateError(f"dataset name is ambiguous, use the id: {ref}")
    return matches[0]


def _load_results(path: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Entries ``{item_id|case_trace, trace_id}``; ``case_trace`` is the case's source trace id."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateError(f"cannot read --results: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise GateError("--results must be a non-empty JSON list")
    by_source = {i["source_trace_id"]: i["id"] for i in items if i.get("source_trace_id")}
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("trace_id"):
            raise GateError("each --results entry needs a trace_id")
        item_id = entry.get("item_id") or by_source.get(entry.get("case_trace", ""))
        if not item_id:
            raise GateError(f"cannot tell which case this entry belongs to: {entry}")
        out.append({"item_id": item_id, "trace_id": entry["trace_id"]})
    return out


async def run_gate(config: Config, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    async with httpx.AsyncClient(
        base_url=config.url.rstrip("/"),
        headers=headers,
        timeout=config.timeout,
        trust_env=False,
        transport=transport,
    ) as client:
        dataset = await _resolve_dataset(client, config.dataset)
        base_path = f"/v1/datasets/{dataset['id']}"

        if config.results_path:
            detail = await _get(client, base_path)
            body = {
                "name": config.name or None,
                "version": {
                    "commit": config.commit or None,
                    "model": config.model or None,
                    "config": config.config,
                },
                "results": _load_results(config.results_path, detail["items"]),
            }
            try:
                response = await client.post(f"{base_path}/runs", json=body)
            except httpx.HTTPError as exc:
                raise GateError(f"Observer unreachable: {exc.__class__.__name__}") from exc
            if response.status_code != 200:
                raise GateError(f"could not record the run: {response.status_code} {response.text}")
            candidate_id = response.json()["id"]
        else:
            candidate_id = ""

        runs = await _get(client, f"{base_path}/runs")  # newest first
        if not candidate_id:
            if config.candidate == "latest":
                if not runs:
                    raise GateError("the dataset has no runs")
                candidate_id = runs[0]["id"]
            else:
                candidate_id = config.candidate
        candidate = next((r for r in runs if r["id"] == candidate_id), None)
        if candidate is None:
            raise GateError(f"candidate run not found in this dataset: {candidate_id}")

        baseline_id = config.baseline
        if baseline_id == "previous":
            older = [r for r in runs if r["created_at"] < candidate["created_at"]]
            baseline_id = older[0]["id"] if older else ""
            if not baseline_id:
                raise GateError("--baseline previous: there is no earlier run")
        comparison = None
        if baseline_id:
            if baseline_id == candidate_id:
                raise GateError("baseline and candidate are the same run")
            comparison = await _get(
                client, f"{base_path}/compare", base=baseline_id, candidate=candidate_id
            )

    verdict = evaluate(candidate, comparison, config.limits)
    return {
        "schema": REPORT_SCHEMA,
        "passed": verdict.passed,
        "failures": verdict.failures,
        "notes": verdict.notes,
        "dataset": {"id": dataset["id"], "name": dataset["name"]},
        "candidate": {k: candidate[k] for k in ("id", "name", "version", "summary")},
        "baseline": comparison and {k: comparison["base"][k] for k in ("id", "name", "version")},
        "comparison": comparison and comparison["summary"],
    }


def _key_values(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {item!r}")
        out[key] = value
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="observer-regression",
        description="Fail a CI job when a version regresses on a dataset of test cases.",
    )
    p.add_argument("--url", default=os.getenv("OBSERVER_URL", DEFAULT_URL))
    p.add_argument("--api-key", default=os.getenv("OBSERVATORY_API_KEY", ""))
    p.add_argument("--dataset", required=True, help="dataset id or name")
    p.add_argument("--candidate", default="latest", help="run id or 'latest' (default)")
    p.add_argument("--baseline", default="", help="run id, 'previous', or empty for none")
    p.add_argument(
        "--results",
        default="",
        help="JSON list of {item_id|case_trace, trace_id}; "
        "records a new run from it and uses it as the candidate",
    )
    p.add_argument("--name", default="")
    p.add_argument("--commit", default="")
    p.add_argument("--model", default="")
    p.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--max-regressions", type=int, default=0)
    p.add_argument("--max-failures", type=int, default=None)
    p.add_argument("--min-pass-rate", type=float, default=None)
    p.add_argument("--max-health-drop", type=float, default=None)
    p.add_argument("--fail-on-unknown", action="store_true")
    p.add_argument("--allow-unverified", action="store_true")
    p.add_argument("--report", default="", help="write the JSON report here")
    return p


def config_from_args(args: argparse.Namespace) -> Config:
    return Config(
        url=args.url,
        api_key=args.api_key,
        dataset=args.dataset,
        candidate=args.candidate,
        baseline=args.baseline,
        results_path=args.results,
        name=args.name,
        commit=args.commit,
        model=args.model,
        config=_key_values(args.config),
        report_path=args.report,
        limits=Thresholds(
            max_regressions=args.max_regressions,
            max_failures=args.max_failures,
            min_pass_rate=args.min_pass_rate,
            max_health_drop=args.max_health_drop,
            fail_on_unknown=args.fail_on_unknown,
            allow_unverified=args.allow_unverified,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        config = config_from_args(build_parser().parse_args(argv))
        report = asyncio.run(run_gate(config))
    except GateError as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "passed": None, "error": str(exc)}))
        return 2
    except argparse.ArgumentTypeError as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "passed": None, "error": str(exc)}))
        return 2
    except KeyboardInterrupt:
        return 130
    if config.report_path:
        Path(config.report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    for line in report["failures"]:
        print(f"FAIL: {line}", file=sys.stderr)
    for line in report["notes"]:
        print(f"note: {line}", file=sys.stderr)
    print(json.dumps({k: report[k] for k in ("schema", "passed", "failures", "notes")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
