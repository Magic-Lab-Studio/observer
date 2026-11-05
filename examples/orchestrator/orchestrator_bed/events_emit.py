"""Render bed episodes as the orchestrator's own JSON events, plus the mapping that reads them
(roadmap step 11, ``POST /v1/ingest/events``).

Each span becomes a ``step.started`` and a ``step.completed`` event, the way a system that never
heard of tracing logs its jobs: a job id (the trace), a step id and its parent step, a type, a
status and a few fields. Every event also carries ``customer_note``, a field the mapping does not
name, so the checks can prove an unmapped field never reaches Observer.
"""

from __future__ import annotations

from typing import Any

from .emit import trace_id
from .model import Episode

UNMAPPED_MARKER = "PRIVATE-NOTE-NOT-MAPPED"


def mapping_yaml(attribute_names: list[str]) -> str:
    attrs = "\n".join(f"      - {name}" for name in sorted(attribute_names))
    return f"""version: 1
event: event
time:
  field: ts
  unit: ms
trace:
  key: job_id
  session: conversation
spans:
  - start: step.started
    end: step.completed
    key: step
    parent: parent_step
    name_field: step_name
    type_field: step_type
    status_field: status
    attributes:
{attrs}
"""


def to_events(episodes: list[Episode], *, project: str, run: str, base_s: float) -> dict[str, Any]:
    """The request body for ``/v1/ingest/events``: the mapping and the events, in time order."""
    events: list[dict[str, Any]] = []
    names: set[str] = set()
    for ep in episodes:
        job = trace_id(project, run, ep)  # a UUID job id is kept as the trace id
        t0_ms = (base_s + ep.offset_s) * 1000
        for s in ep.spans:
            names.update(s.attrs)
            common = {"job_id": job, "conversation": f"conv-{run}-{ep.key}", "step": s.key,
                      "parent_step": s.parent, "step_name": s.name, "step_type": s.span_type,
                      "customer_note": UNMAPPED_MARKER}
            events.append({**common, "event": "step.started", "ts": t0_ms + s.start_ms})
            events.append({**common, **s.attrs, "event": "step.completed",
                           "ts": t0_ms + s.end_ms, "status": s.status})
    events.sort(key=lambda e: e["ts"])
    events.append({"event": "heartbeat", "ts": base_s * 1000})  # no span uses it: ignored
    return {"project_id": project, "environment": "test", "mapping": mapping_yaml(sorted(names)),
            "events": events}
