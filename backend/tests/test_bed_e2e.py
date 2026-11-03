"""The synthetic orchestrator (examples/orchestrator) against the app, in process.

The same episodes and checks run in CI against a real server (``run_bed.py``); here they go
through the ASGI client so every backend change is checked against the planted answers.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples" / "orchestrator"))

from orchestrator_bed import CHECKS, build_episodes, to_batch, to_otlp  # noqa: E402

FORMATS = [("bed-batch", "/v1/traces/batch", to_batch), ("bed-otlp", "/v1/otlp/v1/traces", to_otlp)]


@pytest.mark.anyio
@pytest.mark.parametrize(("project", "path", "render"), FORMATS)
@pytest.mark.parametrize("step", sorted(CHECKS))
async def test_bed_checks_hold(client, project, path, render, step):
    episodes = build_episodes()
    r = await client.post(path, json=render(episodes, project=project, run="test",
                                            base_s=time.time() - 120))
    assert r.status_code == 200, r.text
    failed = [(label, detail) for label, ok, detail in
              await CHECKS[step](client, episodes, project, "test") if not ok]
    assert not failed, failed
