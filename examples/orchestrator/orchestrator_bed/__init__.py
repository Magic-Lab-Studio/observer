"""A synthetic agent orchestrator with planted answers, to test Observer end to end."""

from .checks import CHECKS
from .emit import to_batch, to_otlp, trace_id
from .events_emit import to_events
from .model import Episode, SpanSpec, build_episodes

__all__ = ["CHECKS", "Episode", "SpanSpec", "build_episodes", "to_batch", "to_events", "to_otlp",
           "trace_id"]
