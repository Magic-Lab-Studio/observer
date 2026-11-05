"""``observer-ingest``: send a system's own JSONL events to Observer through a mapping.

    observer-ingest --mapping map.yaml --from events.jsonl --project my-agent [--dry-run]

Events are grouped by trace before batching (a span's start and end must travel together), then
posted to ``POST /v1/ingest/events``. Ingestion is idempotent, so the same file can be sent again
(a backfill of last month, a retry after a failure) without creating duplicates.

``--dry-run`` converts locally and prints the traces it would create; nothing is sent.

Sources: a JSONL file (``-`` reads standard input). Loki and Azure Log Analytics would implement
``EventSource`` the same way; they are not built or verified yet.

Exit status: ``0`` every trace stored (or, with ``--dry-run``, the mapping is valid); ``1`` some
trace was rejected; ``2`` it could not run (bad mapping or file, Observer unreachable or
refusing the request).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.event_mapping import (
    EventMapping,
    MappingError,
    events_to_envelopes,
    group_by_trace,
    load_mapping,
    read_jsonl,
)

DEFAULT_URL = "http://127.0.0.1:8100"
REPORT_SCHEMA = "observer.ingest.v1"


class IngestError(Exception):
    """Could not run (exit 2)."""


class EventSource(Protocol):
    """Where events come from. ``JsonlSource`` is the only one built; Loki and Azure Log
    Analytics would yield the same dicts (unverified, not implemented)."""

    def read(self) -> tuple[list[Any], int]: ...


class JsonlSource:
    def __init__(self, path: str) -> None:
        self.path = path

    def _lines(self) -> Iterator[str]:
        if self.path == "-":
            yield from sys.stdin
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                yield from handle
        except OSError as exc:
            raise IngestError(f"cannot read {self.path}: {exc.strerror}") from None

    def read(self) -> tuple[list[Any], int]:
        return read_jsonl(self._lines())


def dry_run(mapping: EventMapping, events: list[Any], project: str, environment: str) -> dict:
    envelopes, report = events_to_envelopes(mapping, events, project_id=project,
                                            environment=environment)
    return {
        "schema": REPORT_SCHEMA, "dry_run": True, "report": report.as_dict(),
        "traces": [{"trace_id": e["trace"]["id"], "name": e["trace"]["name"],
                    "turn_id": e["turn_id"], "spans": len(e["spans"]),
                    "span_names": sorted({s["name"] for s in e["spans"]})[:20]}
                   for e in envelopes],
    }


async def send(mapping_text: str, mapping: EventMapping, events: list[Any], *, url: str,
               api_key: str, project: str, environment: str, batch_size: int,
               transport: httpx.AsyncBaseTransport | None = None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    totals: dict[str, Any] = {"accepted": 0, "duplicates": 0, "rejected": 0, "errors": [],
                              "batches": 0, "report": {}}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), headers=headers, timeout=60,
                                 trust_env=False, transport=transport) as client:
        for batch in group_by_trace(mapping, events, batch_size):
            try:
                response = await client.post("/v1/ingest/events", json={
                    "project_id": project, "environment": environment,
                    "mapping": mapping_text, "events": batch})
            except httpx.HTTPError as exc:
                reason = exc.__class__.__name__
                raise IngestError(f"Observer unreachable at {url}: {reason}") from None
            if response.status_code != 200:
                raise IngestError(f"Observer answered {response.status_code}: "
                                  f"{response.text[:300]}")
            body = response.json()
            totals["batches"] += 1
            for key in ("accepted", "duplicates", "rejected"):
                totals[key] += int(body.get(key) or 0)
            totals["errors"].extend(body.get("errors") or [])
            for key, value in (body.get("report") or {}).items():
                totals["report"][key] = totals["report"].get(key, 0) + int(value)
    totals["errors"] = totals["errors"][:20]
    return {"schema": REPORT_SCHEMA, "dry_run": False, **totals}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="observer-ingest",
        description="Send a system's own JSONL events to Observer, mapped to traces.",
    )
    p.add_argument("--mapping", required=True, help="YAML mapping file")
    p.add_argument("--from", dest="source", required=True, help="JSONL file, or - for stdin")
    p.add_argument("--project", required=True)
    p.add_argument("--environment", default="production")
    p.add_argument("--url", default=os.getenv("OBSERVER_URL", DEFAULT_URL))
    p.add_argument("--api-key", default=os.getenv("OBSERVATORY_API_KEY", ""))
    p.add_argument("--batch-size", type=int, default=5000, help="events per request (max 20000)")
    p.add_argument("--dry-run", action="store_true", help="convert and print; send nothing")
    return p


def run(args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    try:
        mapping_text = Path(args.mapping).read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(f"cannot read {args.mapping}: {exc.strerror}") from None
    try:
        mapping = load_mapping(mapping_text)
    except MappingError as exc:
        raise IngestError(str(exc)) from None
    events, bad_lines = JsonlSource(args.source).read()
    if args.dry_run:
        report = dry_run(mapping, events, args.project, args.environment)
    else:
        if not 1 <= args.batch_size <= 20_000:
            raise IngestError("--batch-size must be between 1 and 20000")
        report = asyncio.run(send(mapping_text, mapping, events, url=args.url,
                                  api_key=args.api_key, project=args.project,
                                  environment=args.environment, batch_size=args.batch_size,
                                  transport=transport))
    report["bad_lines"] = bad_lines
    return report


def main(argv: list[str] | None = None,
         transport: httpx.AsyncBaseTransport | None = None) -> int:
    try:
        report = run(build_parser().parse_args(argv), transport)
    except IngestError as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "error": str(exc)}))
        return 2
    except KeyboardInterrupt:
        return 130
    print(json.dumps(report, indent=2))
    return 1 if report.get("rejected") else 0


if __name__ == "__main__":
    raise SystemExit(main())
