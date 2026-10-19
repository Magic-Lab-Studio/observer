"""Core tracing engine for LLM Observatory."""

from __future__ import annotations

import asyncio
import atexit
import functools
import logging
import threading
import time
import uuid
import weakref
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Context variable for current span
_current_span: ContextVar[Span | None] = ContextVar("current_span", default=None)


class SpanStatus(str, Enum):
    """Status of a span."""

    OK = "ok"
    ERROR = "error"
    UNSET = "unset"


@dataclass
class TokenUsage:
    """Token usage statistics."""

    input: int = 0
    output: int = 0
    total: int = 0


@dataclass
class Span:
    """A single trace span representing an operation.

    Lifecycle: a span is created active, is finished by ``end()`` and only then handed to its
    tracer's export queue, so what reaches the backend carries its end time, final status,
    tokens, output and cost. ``end()`` is idempotent: later calls change nothing. A span marked
    ERROR stays ERROR; ``end()`` without a status marks an UNSET span OK (the historical default).
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    parent_id: str | None = None
    name: str = ""
    span_type: str = "generic"
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    status: SpanStatus = SpanStatus.UNSET
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    tokens: TokenUsage | None = None
    cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    _tracer: Tracer | None = field(default=None, repr=False, compare=False)

    def end(self, status: SpanStatus | None = None) -> None:
        """Finish the span and queue it for export. Calling it again does nothing."""
        if self.end_time is not None:
            return
        self.end_time = time.time()
        if self.status is not SpanStatus.ERROR:
            if status is not None:
                self.status = status
            elif self.status is SpanStatus.UNSET:
                self.status = SpanStatus.OK
        if self._tracer is not None:
            self._tracer._on_end(self)

    @property
    def ended(self) -> bool:
        """Whether ``end()`` has been called."""
        return self.end_time is not None

    def set_status(self, status: SpanStatus) -> None:
        """Set the span status."""
        self.status = status

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value

    def set_input(self, data: dict[str, Any]) -> None:
        """Set the span input."""
        self.input = data

    def set_output(self, data: dict[str, Any]) -> None:
        """Set the span output."""
        self.output = data

    def set_token_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Set token usage for this span."""
        self.tokens = TokenUsage(
            input=input_tokens,
            output=output_tokens,
            total=input_tokens + output_tokens,
        )

    def set_cost(self, cost_usd: float) -> None:
        """Set the cost in USD for this span."""
        self.cost_usd = cost_usd

    @property
    def duration_ms(self) -> float | None:
        """Get duration in milliseconds."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000


class Tracer:
    """Main tracer class for creating and managing traces."""

    def __init__(
        self,
        service_name: str = "llm-observatory",
        endpoint: str | None = None,
        api_key: str | None = None,
        batch_size: int = 10,
        flush_interval: float = 5.0,
        max_queue_size: int = 10_000,
        max_kept_spans: int = 10_000,
    ) -> None:
        """Initialize the tracer.

        Args:
            service_name: Name of the service being traced.
            endpoint: Endpoint URL for the observatory backend.
            api_key: API key for authentication.
            batch_size: Number of spans to buffer before flushing.
            flush_interval: Seconds between automatic flushes.
            max_queue_size: Finished spans waiting for export; beyond it new ones are dropped
                (and counted in ``dropped_spans``) instead of growing memory without bound.
            max_kept_spans: Spans kept in memory for ``export()``; the oldest are forgotten.
        """
        self.service_name = service_name
        self.endpoint = endpoint
        self.api_key = api_key
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_queue_size = max_queue_size
        self._spans: deque[Span] = deque(maxlen=max_kept_spans)
        self._exporters: list[Any] = []
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._flush_thread: threading.Thread | None = None
        self._shutdown = False
        self.dropped_spans = 0

        # Auto-start flush thread if we have an endpoint
        if endpoint:
            self._start_flush_thread()

    def _start_flush_thread(self) -> None:
        """Start the background thread that sends finished spans, so ``end()`` never waits on
        the network in the caller's thread."""
        if self._flush_thread is not None:
            return
        ref = weakref.ref(self)

        def flush_loop() -> None:
            while True:
                tracer = ref()
                if tracer is None or tracer._shutdown:
                    return
                wake, interval = tracer._wake, tracer.flush_interval
                del tracer
                wake.wait(interval)
                tracer = ref()
                if tracer is None:
                    return
                wake.clear()
                try:
                    tracer.flush()
                except Exception as e:
                    logger.warning(f"Flush failed: {e}")
                del tracer

        self._flush_thread = threading.Thread(
            target=flush_loop, name="observer-flush", daemon=True
        )
        self._flush_thread.start()
        atexit.register(_shutdown_at_exit, ref)

    def add_exporter(self, exporter: Any) -> None:
        """Add an exporter to the tracer.

        Args:
            exporter: Exporter instance with an export(spans) method.
        """
        self._exporters.append(exporter)
        self._start_flush_thread()

    def start_trace(self, name: str, **attributes: Any) -> Span:
        """Start a new trace.

        Args:
            name: Name of the trace.
            **attributes: Additional attributes for the trace.

        Returns:
            The root span of the trace.
        """
        span = Span(
            trace_id=str(uuid.uuid4()),
            name=name,
            span_type="trace",
            attributes=attributes,
            _tracer=self,
        )
        self._spans.append(span)
        return span

    def start_span(
        self,
        name: str,
        span_type: str = "generic",
        parent: Span | None = None,
        **attributes: Any,
    ) -> Span:
        """Start a new span.

        Args:
            name: Name of the span.
            span_type: Type of span (e.g., "llm", "tool", "agent").
            parent: Parent span (defaults to current span).
            **attributes: Additional attributes for the span.

        Returns:
            The new span.
        """
        parent_span = parent or _current_span.get()
        span = Span(
            trace_id=parent_span.trace_id if parent_span else str(uuid.uuid4()),
            parent_id=parent_span.id if parent_span else None,
            name=name,
            span_type=span_type,
            attributes=attributes,
            _tracer=self,
        )
        self._spans.append(span)
        return span

    def _on_end(self, span: Span) -> None:
        """Queue a finished span; a full batch wakes the flush thread."""
        if not self._exporters:
            return
        span_dict = self._span_to_dict(span)
        with self._lock:
            if len(self._buffer) >= self.max_queue_size:
                self.dropped_spans += 1
                full = True
            else:
                self._buffer.append(span_dict)
                full = False
            ready = len(self._buffer) >= self.batch_size
        if full:
            logger.warning("Export queue full (%d spans); span dropped", self.max_queue_size)
        if ready:
            self._wake.set()

    def _span_to_dict(self, span: Span) -> dict[str, Any]:
        """Convert a span to a dictionary for export."""
        return {
            "id": span.id,
            "trace_id": span.trace_id,
            "parent_id": span.parent_id,
            "name": span.name,
            "span_type": span.span_type,
            "start_time": span.start_time,
            "end_time": span.end_time,
            "status": span.status.value,
            "input": span.input,
            "output": span.output,
            "tokens_input": span.tokens.input if span.tokens else None,
            "tokens_output": span.tokens.output if span.tokens else None,
            "cost_usd": span.cost_usd,
            "metadata": span.metadata,
            "attributes": span.attributes,
        }

    def export(self) -> list[dict[str, Any]]:
        """Export all spans as dictionaries.

        Returns:
            List of span dictionaries.
        """
        return [self._span_to_dict(span) for span in self._spans]

    def flush(self) -> None:
        """Send the finished spans waiting in the queue (the network call runs outside the
        lock, so other threads can keep ending spans meanwhile)."""
        with self._lock:
            if not self._buffer:
                return
            spans_to_export = self._buffer.copy()
            self._buffer.clear()

        for exporter in self._exporters:
            try:
                exporter.export(spans_to_export)
            except Exception as e:
                logger.warning(f"Exporter failed: {e}")

    def clear(self) -> None:
        """Clear all collected spans."""
        self._spans.clear()

    def shutdown(self) -> None:
        """Shutdown the tracer, sending every finished span still queued. Spans that were never
        ended are not sent: they have no end time or final status."""
        if self._shutdown:
            return
        self._shutdown = True
        self._wake.set()
        thread = self._flush_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.flush_interval))
        self.flush()
        for exporter in self._exporters:
            try:
                exporter.flush()
            except Exception:
                pass


def _shutdown_at_exit(ref: weakref.ReferenceType[Tracer]) -> None:
    tracer = ref()
    if tracer is not None:
        try:
            tracer.shutdown()
        except Exception:
            logger.debug("Tracer shutdown at exit failed", exc_info=True)


def _record_error(span: Span, exc: BaseException) -> None:
    """An exception, a cancellation or an interrupt ends the operation unsuccessfully."""
    span.set_status(SpanStatus.ERROR)
    span.set_attribute("error.type", type(exc).__name__)
    span.set_attribute("error.message", str(exc))


def trace(
    name: str | None = None,
    span_type: str = "function",
) -> Callable:
    """Decorator to trace a function (sync or async).

    Args:
        name: Name for the trace (defaults to function name).
        span_type: Type of span.

    Returns:
        Decorated function.
    """

    def decorator(func: Callable) -> Callable:
        span_name = name or func.__name__

        if asyncio.iscoroutinefunction(func):
            # Async version
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = _get_global_tracer()
                span = tracer.start_span(span_name, span_type=span_type)
                token = _current_span.set(span)

                try:
                    result = await func(*args, **kwargs)
                    span.set_status(SpanStatus.OK)
                    return result
                except BaseException as e:
                    _record_error(span, e)
                    raise
                finally:
                    span.end()
                    _current_span.reset(token)

            return async_wrapper
        else:
            # Sync version
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = _get_global_tracer()
                span = tracer.start_span(span_name, span_type=span_type)
                token = _current_span.set(span)

                try:
                    result = func(*args, **kwargs)
                    span.set_status(SpanStatus.OK)
                    return result
                except BaseException as e:
                    _record_error(span, e)
                    raise
                finally:
                    span.end()
                    _current_span.reset(token)

            return sync_wrapper

    return decorator


# Global tracer instance
_global_tracer: Tracer | None = None


def _get_global_tracer() -> Tracer:
    """Get or create the global tracer instance."""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = Tracer()
    return _global_tracer


def get_tracer() -> Tracer:
    """Get the global tracer instance."""
    return _get_global_tracer()


def set_tracer(tracer: Tracer) -> None:
    """Set the global tracer instance."""
    global _global_tracer
    _global_tracer = tracer
