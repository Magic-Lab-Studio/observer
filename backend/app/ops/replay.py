"""``observer-replay``: run a dataset's cases N times against an agent, record the runs (step 12).

Observer stores no case inputs (datasets keep metadata only), so the inputs come from a local cases
file: the dataset export (``GET /v1/datasets/{id}/export``) with an ``input`` added to each item, or
a plain list ``[{"item_id" | "case_trace", "input"}]``. The inputs never reach Observer.

For every case and repetition the command POSTs
``{<input-field>: input, "case": id, "repetition": n}`` to the agent's endpoint. The agent
exports its own trace to Observer as usual and answers with the trace id: a JSON field
(``--trace-field``, dots for nesting) or the ``X-Observer-Trace-Id`` header. The pairs
(case, trace, repetition) are written to ``--out`` in the format ``observer-regression --results``
reads; ``--record`` also reports them as a run, so each case shows as ``pass``, ``fail`` or
``unstable`` (a case right 3 times out of 5 is unstable, not pass).

Real sessions (``--session FILE.jsonl``, repeatable, instead of ``--cases``): every user turn of a
conversation log is a case, and the conversation is replayed in order, turn by turn, once per
repetition. Each body then also carries ``conversation`` (``<file stem>:<repetition>``) and ``turn``
so the agent keeps the context between turns, and different repetitions never share it. A line is a
user turn when it has ``role`` ``user``/``human`` and a ``content`` or ``text``, or an ``input``;
other lines are skipped. A turn names its case by the trace it produced (``--session-trace-field``,
default ``trace_id``, also read from ``metadata``); without one it is ``<stem>#<n>``, which can be
written with ``--out`` but not recorded (Observer matches cases by dataset item or source trace).

Exit codes: ``0`` every call returned a trace; ``1`` some calls failed (the rest are written and
recorded); ``2`` nothing could be run (bad arguments, unreadable cases, no call returned a trace,
Observer refused the run).
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

DEFAULT_URL = "http://127.0.0.1:8100"
TRACE_HEADER = "x-observer-trace-id"


class ReplayError(Exception):
    """Nothing could be run (exit code 2)."""


@dataclass
class Case:
    item_id: str  # the dataset item id, or "" when only the source trace is known
    case_trace: str
    input: Any


@dataclass
class Config:
    cases_path: str
    endpoint: str
    sessions: list[str] = field(default_factory=list)
    session_trace_field: str = "trace_id"
    repeat: int = 1
    out_path: str = ""
    input_field: str = "input"
    trace_field: str = "trace_id"
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 120.0
    concurrency: int = 1
    pause: float = 0.0
    record: bool = False
    url: str = DEFAULT_URL
    api_key: str = ""
    dataset: str = ""
    name: str = ""
    commit: str = ""
    model: str = ""
    config: dict[str, str] = field(default_factory=dict)


def load_cases(path: str) -> tuple[list[Case], str]:
    """The cases with an input, and the dataset id when the file is an Observer export."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReplayError(f"cannot read --cases: {exc}") from exc
    dataset_id = ""
    if isinstance(raw, dict):
        dataset_id = str(raw.get("id") or "")
        raw = raw.get("items")
    if not isinstance(raw, list) or not raw:
        raise ReplayError("--cases must be a dataset export or a non-empty JSON list of cases")
    cases, missing = [], []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ReplayError(f"a case must be an object: {entry!r}")
        item_id = str(entry.get("item_id") or (entry.get("id") if dataset_id else "") or "")
        case_trace = str(entry.get("case_trace") or entry.get("source_trace_id") or "")
        if not item_id and not case_trace:
            raise ReplayError(f"a case needs item_id (or id in an export) or case_trace: {entry!r}")
        if entry.get("input") in (None, ""):
            missing.append(item_id or case_trace)
            continue
        cases.append(Case(item_id=item_id, case_trace=case_trace, input=entry["input"]))
    if missing:
        # Observer keeps no inputs: an export must be completed locally before it can be replayed.
        raise ReplayError(
            f"{len(missing)} case(s) have no input (add an 'input' to each): {missing[:5]}"
        )
    return cases, dataset_id


_USER_ROLES = {"user", "human"}


def load_session(path: str, trace_field: str = "trace_id") -> list[Case]:
    """The user turns of a JSONL conversation log, in order."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReplayError(f"cannot read --session: {exc}") from exc
    stem, cases = Path(path).stem, []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a torn last line of a live log
        if not isinstance(entry, dict):
            continue
        if "input" in entry and "role" not in entry:
            text = entry.get("input")
        elif str(entry.get("role") or "").lower() in _USER_ROLES:
            text = (
                entry.get("content")
                if entry.get("content") not in (None, "")
                else entry.get("text")
            )
        else:
            continue
        if text in (None, "") or (isinstance(text, str) and not text.strip()):
            continue
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        trace = _dig(entry, trace_field) or _dig(metadata, trace_field)
        cases.append(
            Case(
                item_id="",
                case_trace=str(trace)
                if isinstance(trace, str) and trace
                else f"{stem}#{len(cases)}",
                input=text,
            )
        )
    if not cases:
        raise ReplayError(f"--session {path} has no user turns")
    return cases


def _has_trace(case: Case) -> bool:
    return "#" not in case.case_trace


def _dig(data: Any, dotted: str) -> Any:
    for key in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


async def _call(
    client: httpx.AsyncClient,
    config: Config,
    case: Case,
    repetition: int,
    conversation: tuple[str, int] | None = None,
) -> dict[str, Any]:
    body = {
        config.input_field: case.input,
        "case": case.item_id or case.case_trace,
        "repetition": repetition,
    }
    if conversation is not None:
        body["conversation"], body["turn"] = conversation
    row: dict[str, Any] = {"repetition": repetition}
    row["item_id" if case.item_id else "case_trace"] = case.item_id or case.case_trace
    try:
        response = await client.post(config.endpoint, json=body)
    except httpx.HTTPError as exc:
        return {**row, "error": f"{exc.__class__.__name__}"}
    if response.status_code >= 400:
        return {**row, "error": f"HTTP {response.status_code}"}
    trace_id = None
    try:
        trace_id = _dig(response.json(), config.trace_field)
    except ValueError:
        pass
    trace_id = trace_id or response.headers.get(TRACE_HEADER)
    if not trace_id or not isinstance(trace_id, str):
        return {**row, "error": "no trace id in the reply"}
    return {**row, "trace_id": trace_id}


async def replay(
    config: Config, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, Any]]:
    """Every (case, repetition) in order; a failed call carries ``error``, not ``trace_id``."""
    gate = asyncio.Semaphore(max(1, config.concurrency))
    async with httpx.AsyncClient(
        headers=config.headers, timeout=config.timeout, trust_env=False, transport=transport
    ) as client:
        if config.sessions:
            sessions = [
                (Path(p).stem, load_session(p, config.session_trace_field)) for p in config.sessions
            ]

            async def conversation(stem: str, turns: list[Case], n: int) -> list[dict[str, Any]]:
                # One conversation at a time per slot: its turns must arrive in order.
                async with gate:
                    out = []
                    for i, case in enumerate(turns):
                        out.append(await _call(client, config, case, n, (f"{stem}:{n}", i)))
                        if config.pause:
                            await asyncio.sleep(config.pause)
                    return out

            runs = await asyncio.gather(
                *(
                    conversation(stem, turns, n)
                    for stem, turns in sessions
                    for n in range(config.repeat)
                )
            )
            return [row for rows in runs for row in rows]

        cases, _ = load_cases(config.cases_path)
        jobs = [(case, n) for case in cases for n in range(config.repeat)]

        async def one(case: Case, n: int) -> dict[str, Any]:
            async with gate:
                result = await _call(client, config, case, n)
                if config.pause:
                    await asyncio.sleep(config.pause)
                return result

        return list(await asyncio.gather(*(one(case, n) for case, n in jobs)))


async def record_run(
    config: Config,
    results: list[dict[str, Any]],
    dataset_id: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Report the traced calls as a run (Observer checks each case against its trace)."""
    from app.ops.regression_gate import GateError, _resolve_dataset

    traced = [r for r in results if r.get("trace_id")]
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    async with httpx.AsyncClient(
        base_url=config.url.rstrip("/"),
        headers=headers,
        timeout=30,
        trust_env=False,
        transport=transport,
    ) as client:
        try:
            dataset = await _resolve_dataset(client, config.dataset or dataset_id)
            detail = (await client.get(f"/v1/datasets/{dataset['id']}")).json()
            rows = _match(traced, detail["items"])
        except (GateError, httpx.HTTPError, KeyError, ValueError) as exc:
            raise ReplayError(f"could not read the dataset from Observer: {exc}") from exc
        body = {
            "name": config.name or None,
            "version": {
                "commit": config.commit or None,
                "model": config.model or None,
                "config": config.config,
            },
            "results": rows,
        }
        try:
            response = await client.post(f"/v1/datasets/{dataset['id']}/runs", json=body)
        except httpx.HTTPError as exc:
            raise ReplayError(f"Observer unreachable: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            raise ReplayError(
                f"Observer refused the run: {response.status_code} {response.text[:300]}"
            )
        return response.json()


def _match(traced: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source = {i["source_trace_id"]: i["id"] for i in items if i.get("source_trace_id")}
    known = {i["id"] for i in items}
    rows = []
    for entry in traced:
        item_id = entry.get("item_id") or by_source.get(entry.get("case_trace", ""))
        if not item_id or item_id not in known:
            raise ReplayError(
                "this case is not in the dataset: "
                f"{entry.get('item_id') or entry.get('case_trace')}"
            )
        rows.append(
            {"item_id": item_id, "trace_id": entry["trace_id"], "repetition": entry["repetition"]}
        )
    return rows


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
        prog="observer-replay",
        description="Run a dataset's cases N times against an agent endpoint and record the runs.",
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--cases", help="dataset export with an 'input' per item, or a JSON list")
    source.add_argument(
        "--session",
        action="append",
        default=[],
        metavar="FILE.jsonl",
        help="a real conversation log: its user turns are replayed in order (repeatable)",
    )
    p.add_argument(
        "--session-trace-field",
        default="trace_id",
        help="field of a session line naming the trace that turn produced (a.b.c)",
    )
    p.add_argument("--endpoint", required=True, help="the agent's HTTP endpoint (POST)")
    p.add_argument("--repeat", type=int, default=1, help="runs per case (1-99)")
    p.add_argument(
        "--out", default="", help="write the results (observer-regression --results format)"
    )
    p.add_argument("--input-field", default="input")
    p.add_argument(
        "--trace-field", default="trace_id", help="JSON path of the trace id in the reply (a.b.c)"
    )
    p.add_argument(
        "--header", action="append", default=[], metavar="KEY=VALUE", help="sent to the endpoint"
    )
    p.add_argument("--timeout", type=float, default=120.0, help="seconds per call")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--pause", type=float, default=0.0, help="seconds after each call (rate limits)")
    p.add_argument("--record", action="store_true", help="also report the run to Observer")
    p.add_argument("--url", default=os.getenv("OBSERVER_URL", DEFAULT_URL))
    p.add_argument("--api-key", default=os.getenv("OBSERVATORY_API_KEY", ""))
    p.add_argument("--dataset", default="", help="dataset id or name (default: the export's id)")
    p.add_argument("--name", default="")
    p.add_argument("--commit", default="")
    p.add_argument("--model", default="")
    p.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    return p


def config_from_args(args: argparse.Namespace) -> Config:
    if not 1 <= args.repeat <= 99:
        raise ReplayError("--repeat must be between 1 and 99")
    try:
        headers, extra = _key_values(args.header), _key_values(args.config)
    except argparse.ArgumentTypeError as exc:
        raise ReplayError(str(exc)) from exc
    return Config(
        cases_path=args.cases or "",
        endpoint=args.endpoint,
        sessions=list(args.session or []),
        session_trace_field=args.session_trace_field,
        repeat=args.repeat,
        out_path=args.out,
        input_field=args.input_field,
        trace_field=args.trace_field,
        headers=headers,
        timeout=args.timeout,
        concurrency=args.concurrency,
        pause=args.pause,
        record=args.record,
        url=args.url,
        api_key=args.api_key,
        dataset=args.dataset,
        name=args.name,
        commit=args.commit,
        model=args.model,
        config=extra,
    )


async def run(
    config: Config,
    transport: httpx.AsyncBaseTransport | None = None,
    observer_transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    if config.sessions:
        dataset_id = ""
        loaded = [load_session(p, config.session_trace_field) for p in config.sessions]
        if config.record:
            untraced = [c.case_trace for turns in loaded for c in turns if not _has_trace(c)]
            if untraced:
                raise ReplayError(
                    "--record needs every session turn to name its trace "
                    f"(--session-trace-field); these do not: {untraced[:5]}"
                )
    else:
        _, dataset_id = load_cases(config.cases_path)
    if config.record and not (config.dataset or dataset_id):
        raise ReplayError("--record needs --dataset (or a dataset export as --cases)")
    results = await replay(config, transport)
    traced = [r for r in results if r.get("trace_id")]
    if not traced:
        errors = sorted({r["error"] for r in results})
        raise ReplayError(f"no call returned a trace id: {errors[:3]}")
    if config.out_path:
        Path(config.out_path).write_text(json.dumps(traced, indent=2), encoding="utf-8")
    report: dict[str, Any] = {
        "calls": len(results),
        "traced": len(traced),
        "failed": [r for r in results if r.get("error")],
    }
    if config.record:
        run_out = await record_run(config, results, dataset_id, observer_transport)
        report["run"] = {k: run_out.get(k) for k in ("id", "summary")}
        report["unstable"] = run_out.get("cases") or []
    return (1 if report["failed"] else 0), report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code, report = asyncio.run(run(config_from_args(args)))
    except ReplayError as exc:
        print(f"observer-replay: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
