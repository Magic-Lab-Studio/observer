"""A span as a ``with`` block: the public way to open a turn's root (or any span) without a
decorator, so child spans (instrumented model calls, ``agent.*`` helpers) nest under it."""

import pytest

import llm_observatory
from llm_observatory import agent, current_span, span
from llm_observatory.tracer import SpanStatus, Tracer, set_tracer


@pytest.fixture
def tracer():
    t = Tracer(service_name="test")
    set_tracer(t)
    return t


def test_span_block_is_current_and_parents_its_children(tracer):
    with span("turn", span_type="agent", task="support") as root:
        assert current_span() is root
        root.set_input({"text": "how much?"})
        with agent.retrieval("docs") as r:
            r.found(2, sources=["prices"])
        with tracer.span("format") as child:
            assert child.parent_id == root.id and child.trace_id == root.trace_id
        root.set_output({"text": "14 USD"})
    assert current_span() is None
    assert root.ended and root.status == SpanStatus.OK
    assert root.attributes["task"] == "support" and root.span_type == "agent"
    retrieval = next(s for s in tracer._spans if s.span_type == "retrieval")
    assert retrieval.parent_id == root.id


def test_an_exception_marks_the_span_and_propagates(tracer):
    with pytest.raises(ValueError):
        with span("turn") as s:
            raise ValueError("boom")
    assert s.status == SpanStatus.ERROR and s.attributes["error.type"] == "ValueError"
    assert current_span() is None


@pytest.mark.anyio
async def test_works_in_async_code(tracer):
    with span("turn") as s:
        assert current_span() is s
    assert s.ended


def test_exported_at_package_level():
    assert llm_observatory.span is span and "span" in llm_observatory.__all__
