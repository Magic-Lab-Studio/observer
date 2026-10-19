"""C2 of the 2026-10-18 evaluation: a span reaches the exporter once, finished, with what was
recorded on it before end(); finalization never turns an error into a success."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from llm_observatory.tracer import SpanStatus, Tracer, set_tracer, trace


class Capture:
    def __init__(self, delay: float = 0.0) -> None:
        self.spans: list[dict] = []
        self.delay = delay
        self.lock = threading.Lock()

    def export(self, spans: list[dict]) -> None:
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.spans.extend(spans)

    def flush(self) -> None:
        pass


def _tracer(**kwargs) -> tuple[Tracer, Capture]:
    tracer = Tracer(batch_size=kwargs.pop("batch_size", 1000), **kwargs)
    sink = Capture()
    tracer.add_exporter(sink)
    return tracer, sink


def test_exported_span_carries_what_was_recorded_before_end():
    tracer, sink = _tracer()
    root = tracer.start_trace("agent")
    llm = tracer.start_span("llm", span_type="llm", parent=root)
    llm.set_token_usage(10, 4)
    llm.set_output({"text": "hi"})
    llm.set_cost(0.002)
    llm.end()
    root.end()
    tracer.shutdown()
    by_name = {s["name"]: s for s in sink.spans}
    assert set(by_name) == {"agent", "llm"}
    child = by_name["llm"]
    assert child["status"] == "ok" and child["end_time"] >= child["start_time"]
    assert (child["tokens_input"], child["tokens_output"]) == (10, 4)
    assert child["output"] == {"text": "hi"} and child["cost_usd"] == 0.002
    assert child["parent_id"] == by_name["agent"]["id"]
    assert child["trace_id"] == by_name["agent"]["trace_id"]


def test_end_is_idempotent_and_exports_once():
    tracer, sink = _tracer()
    span = tracer.start_span("s")
    span.end()
    first_end = span.end_time
    span.end(SpanStatus.ERROR)
    tracer.shutdown()
    assert span.end_time == first_end and span.status == SpanStatus.OK
    assert len(sink.spans) == 1


@pytest.mark.parametrize("final", [None, SpanStatus.OK])
def test_an_error_is_never_turned_into_ok_at_end(final):
    tracer, sink = _tracer()
    span = tracer.start_span("s")
    span.set_status(SpanStatus.ERROR)
    span.end(final) if final is not None else span.end()
    tracer.shutdown()
    assert span.status == SpanStatus.ERROR and sink.spans[0]["status"] == "error"


def test_an_unfinished_span_is_never_exported():
    tracer, sink = _tracer()
    tracer.start_span("still running")
    done = tracer.start_span("done")
    done.end()
    tracer.shutdown()
    assert [s["name"] for s in sink.spans] == ["done"]


def test_end_does_not_wait_for_the_network_in_the_callers_thread():
    tracer = Tracer(batch_size=1, flush_interval=0.05)
    slow = Capture(delay=0.5)
    tracer.add_exporter(slow)
    started = time.perf_counter()
    for _ in range(5):
        tracer.start_span("s").end()
    elapsed = time.perf_counter() - started
    tracer.shutdown()
    assert elapsed < 0.2, elapsed
    assert len(slow.spans) == 5


def test_a_full_batch_is_sent_without_waiting_for_shutdown():
    tracer = Tracer(batch_size=3, flush_interval=30)
    sink = Capture()
    tracer.add_exporter(sink)
    for _ in range(3):
        tracer.start_span("s").end()
    deadline = time.time() + 2
    while len(sink.spans) < 3 and time.time() < deadline:
        time.sleep(0.01)
    assert len(sink.spans) == 3
    tracer.shutdown()


def test_concurrent_threads_export_every_span_exactly_once():
    tracer, sink = _tracer(batch_size=7, flush_interval=0.01)

    def work():
        for _ in range(200):
            tracer.start_span("s").end()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    tracer.shutdown()
    ids = [s["id"] for s in sink.spans]
    assert len(ids) == 1600 and len(set(ids)) == 1600


def test_a_full_queue_drops_and_counts_instead_of_growing():
    tracer = Tracer(batch_size=10_000, flush_interval=30, max_queue_size=3)
    sink = Capture()
    tracer.add_exporter(sink)
    for _ in range(5):
        tracer.start_span("s").end()
    assert tracer.dropped_spans == 2
    tracer.shutdown()
    assert len(sink.spans) == 3


def test_shutdown_twice_is_harmless():
    tracer, sink = _tracer()
    tracer.start_span("s").end()
    tracer.shutdown()
    tracer.shutdown()
    assert len(sink.spans) == 1


@pytest.fixture
def global_capture():
    tracer, sink = _tracer()
    set_tracer(tracer)
    yield tracer, sink
    tracer.shutdown()
    set_tracer(Tracer())


def test_decorated_function_that_raises_exports_an_error_span(global_capture):
    tracer, sink = global_capture

    @trace(name="tool")
    def tool():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        tool()
    tracer.flush()
    span = sink.spans[0]
    assert span["status"] == "error" and span["end_time"] is not None
    assert span["attributes"]["error.type"] == "ValueError"


def test_a_cancelled_task_is_not_recorded_as_a_success(global_capture):
    tracer, sink = global_capture

    @trace(name="slow")
    async def slow():
        await asyncio.sleep(10)

    async def main():
        task = asyncio.create_task(slow())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    tracer.flush()
    span = sink.spans[0]
    assert span["status"] == "error" and span["attributes"]["error.type"] == "CancelledError"


def test_nested_decorated_functions_keep_parentage(global_capture):
    tracer, sink = global_capture

    @trace(name="inner")
    def inner():
        return 1

    @trace(name="outer")
    def outer():
        return inner() + 1

    assert outer() == 2
    tracer.flush()
    by_name = {s["name"]: s for s in sink.spans}
    assert by_name["inner"]["parent_id"] == by_name["outer"]["id"]
    # the inner span finishes first, so it is queued first
    assert [s["name"] for s in sink.spans] == ["inner", "outer"]
