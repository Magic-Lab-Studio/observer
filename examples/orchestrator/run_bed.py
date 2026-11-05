#!/usr/bin/env python3
"""Send the synthetic orchestrator's episodes to a running Observer and check its answers.

Each format goes to its own project (``bed-batch``: the SDK wire format; ``bed-otlp``: OTLP/JSON),
then every registered check compares what Observer's public API says with the planted truth.

    python examples/orchestrator/run_bed.py --endpoint http://127.0.0.1:8000 [--check all]

Exit status 0 when every check holds, 1 otherwise. No model is called and nothing leaves the
machine except the requests to ``--endpoint``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchestrator_bed import CHECKS, build_episodes, to_batch, to_otlp
from orchestrator_bed.sdk_emit import check_sdk, to_sdk

FORMATS = {"batch": ("bed-batch", "/v1/traces/batch", to_batch),
           "otlp": ("bed-otlp", "/v1/otlp/v1/traces", to_otlp)}


async def run(endpoint: str, api_key: str | None, formats: list[str], checks: list[str],
              seed: int) -> int:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    episodes = build_episodes(seed)
    run_id = uuid.uuid4().hex[:8]
    base = time.time() - 120
    failures = 0
    async with httpx.AsyncClient(base_url=endpoint, headers=headers, timeout=30) as api:
        for fmt in (f for f in formats if f != "sdk"):
            project, path, render = FORMATS[fmt]
            r = await api.post(path, json=render(episodes, project=project, run=run_id, base_s=base))
            ok = r.status_code == 200
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  sent {len(episodes)} episodes as {fmt}"
                  + ("" if ok else f"  [{r.status_code} {r.text[:200]}]"))
        if "sdk" in formats:
            body, truths = to_sdk("bed-sdk")
            r = await api.post("/v1/traces/batch", json=body)
            failures += r.status_code != 200
            for label, ok, detail in await check_sdk(api, truths, "bed-sdk"):
                failures += not ok
                print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  [{detail}]"))
        for fmt in formats:
            if fmt == "sdk":
                continue
            project = FORMATS[fmt][0]
            for step in checks:
                for label, ok, detail in await CHECKS[step](api, episodes, project, run_id):
                    failures += not ok
                    print(f"{'PASS' if ok else 'FAIL'}  {label}"
                          + (f"  [{detail}]" if detail and not ok else ""))
    print(f"{failures} failed" if failures else "all checks passed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--emit", default="batch,otlp,sdk",
                        help="comma list: batch, otlp, sdk (the Python SDK helpers)")
    parser.add_argument("--check", default="all", help="comma list of steps, or all")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    formats = [f for f in args.emit.split(",") if f]
    checks = sorted(CHECKS) if args.check == "all" else args.check.split(",")
    unknown = [f for f in formats if f not in FORMATS and f != "sdk"]
    unknown += [c for c in checks if c not in CHECKS]
    if unknown:
        parser.error(f"unknown: {unknown}")
    return asyncio.run(run(args.endpoint, args.api_key, formats, checks, args.seed))


if __name__ == "__main__":
    raise SystemExit(main())
