"""C1/C2 of the 2026-10-18 evaluation: the instrumentors against the real Anthropic and OpenAI SDK
clients, with a mocked HTTP transport (no key, no network). An instrumented call must behave like
the uninstrumented one, and the span it leaves must be finished: end time, status, tokens."""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

try:  # current provider SDKs ship their own fork of httpx and refuse a plain httpx client
    import httpx2 as httpx
except ImportError:
    import httpx

from llm_observatory.instrumentors._patching import _unwrap
from llm_observatory.instrumentors.anthropic import AnthropicInstrumentor
from llm_observatory.instrumentors.openai import OpenAIInstrumentor
from llm_observatory.tracer import Tracer, set_tracer

anthropic = pytest.importorskip("anthropic")
openai = pytest.importorskip("openai")

ANTHROPIC_OK = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-5-20250929",
    "content": [{"type": "text", "text": "hola"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 12, "output_tokens": 3},
}
OPENAI_OK = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hola"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
}
ANTHROPIC_ERROR = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
OPENAI_ERROR = {"error": {"message": "bad", "type": "invalid_request_error"}}


class Capture:
    def __init__(self) -> None:
        self.spans: list[dict] = []

    def export(self, spans: list[dict]) -> None:
        self.spans.extend(spans)

    def flush(self) -> None:
        pass


@pytest.fixture
def capture():
    tracer = Tracer(batch_size=1000)
    sink = Capture()
    tracer.add_exporter(sink)
    set_tracer(tracer)
    yield tracer, sink
    tracer.shutdown()
    set_tracer(Tracer())


def _responder(body: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


def _async_responder(body: dict, status: int = 200):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


def _anthropic(body=ANTHROPIC_OK, status=200):
    transport = httpx.MockTransport(_responder(body, status))
    return anthropic.Anthropic(
        api_key="test", max_retries=0, http_client=httpx.Client(transport=transport)
    )


def _async_anthropic(body=ANTHROPIC_OK, status=200):
    transport = httpx.MockTransport(_async_responder(body, status))
    return anthropic.AsyncAnthropic(
        api_key="test", max_retries=0, http_client=httpx.AsyncClient(transport=transport)
    )


def _openai(body=OPENAI_OK, status=200):
    transport = httpx.MockTransport(_responder(body, status))
    return openai.OpenAI(
        api_key="test", max_retries=0, http_client=httpx.Client(transport=transport)
    )


def _async_openai(body=OPENAI_OK, status=200):
    transport = httpx.MockTransport(_async_responder(body, status))
    return openai.AsyncOpenAI(
        api_key="test", max_retries=0, http_client=httpx.AsyncClient(transport=transport)
    )


@pytest.fixture
def anthropic_instrumented():
    original_sync = anthropic.resources.messages.Messages.create
    original_async = anthropic.resources.messages.AsyncMessages.create
    inst = AnthropicInstrumentor()
    inst.instrument()
    yield inst
    inst.uninstrument()
    assert anthropic.resources.messages.Messages.create is original_sync
    assert anthropic.resources.messages.AsyncMessages.create is original_async


@pytest.fixture
def openai_instrumented():
    original_sync = openai.resources.chat.completions.Completions.create
    original_async = openai.resources.chat.completions.AsyncCompletions.create
    inst = OpenAIInstrumentor()
    inst.instrument()
    yield inst
    inst.uninstrument()
    assert openai.resources.chat.completions.Completions.create is original_sync
    assert openai.resources.chat.completions.AsyncCompletions.create is original_async


MESSAGES = [{"role": "user", "content": "hi"}]


def _only_llm_span(sink: Capture) -> dict:
    spans = [s for s in sink.spans if s["span_type"] == "llm"]
    assert len(spans) == 1, spans
    return spans[0]


def _assert_finished_ok(span: dict, tokens_in: int, tokens_out: int) -> None:
    assert span["status"] == "ok"
    assert span["end_time"] is not None and span["end_time"] >= span["start_time"]
    assert span["tokens_input"] == tokens_in and span["tokens_output"] == tokens_out
    assert span["output"]


def test_anthropic_sync_call_works_and_leaves_a_finished_span(capture, anthropic_instrumented):
    tracer, sink = capture
    result = _anthropic().messages.create(
        model="claude-sonnet-4-5-20250929", max_tokens=10, messages=MESSAGES
    )
    assert result.content[0].text == "hola"
    tracer.flush()
    span = _only_llm_span(sink)
    _assert_finished_ok(span, 12, 3)
    assert span["attributes"]["gen_ai.response.finish_reasons"] == ["end_turn"]
    assert span["attributes"]["gen_ai.response.model"] == "claude-sonnet-4-5-20250929"
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 12


def test_anthropic_async_call_works_and_leaves_a_finished_span(capture, anthropic_instrumented):
    tracer, sink = capture
    patched = anthropic.resources.messages.AsyncMessages.create
    assert inspect.iscoroutinefunction(patched) == inspect.iscoroutinefunction(_unwrap(patched))

    async def call():
        client = _async_anthropic()
        return await client.messages.create(
            model="claude-sonnet-4-5-20250929", max_tokens=10, messages=MESSAGES
        )

    result = asyncio.run(call())
    assert result.usage.output_tokens == 3
    tracer.flush()
    _assert_finished_ok(_only_llm_span(sink), 12, 3)


def test_anthropic_error_propagates_unchanged_and_the_span_is_an_error(
    capture, anthropic_instrumented
):
    tracer, sink = capture
    with pytest.raises(anthropic.BadRequestError):
        _anthropic(ANTHROPIC_ERROR, 400).messages.create(
            model="claude-sonnet-4-5", max_tokens=10, messages=MESSAGES
        )
    tracer.flush()
    span = _only_llm_span(sink)
    assert span["status"] == "error" and span["end_time"] is not None
    assert "bad" in json.dumps(span["attributes"])


def test_openai_sync_and_async_calls_work(capture, openai_instrumented):
    tracer, sink = capture
    result = _openai().chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    assert result.choices[0].message.content == "hola"

    async def call():
        return await _async_openai().chat.completions.create(
            model="gpt-4o-mini", messages=MESSAGES
        )

    assert asyncio.run(call()).usage.total_tokens == 9
    tracer.flush()
    spans = [s for s in sink.spans if s["span_type"] == "llm"]
    assert len(spans) == 2
    for span in spans:
        _assert_finished_ok(span, 7, 2)
        assert span["cost_usd"] is not None


def test_openai_error_propagates_and_the_span_is_an_error(capture, openai_instrumented):
    tracer, sink = capture
    with pytest.raises(openai.BadRequestError):
        _openai(OPENAI_ERROR, 400).chat.completions.create(model="gpt-4o", messages=MESSAGES)
    tracer.flush()
    span = _only_llm_span(sink)
    assert span["status"] == "error" and span["end_time"] is not None


def test_instrumenting_twice_records_one_span_per_call(capture):
    tracer, sink = capture
    first, second = AnthropicInstrumentor(), AnthropicInstrumentor()
    original = anthropic.resources.messages.Messages.create
    first.instrument()
    first.instrument()
    second.instrument()
    try:
        _anthropic().messages.create(model="claude-sonnet-4-5", max_tokens=10, messages=MESSAGES)
    finally:
        second.uninstrument()
        first.uninstrument()
    assert anthropic.resources.messages.Messages.create is original
    tracer.flush()
    _only_llm_span(sink)


def test_llm_span_nests_under_the_current_span(capture, anthropic_instrumented):
    tracer, sink = capture
    from llm_observatory.tracer import trace

    @trace(name="agent", span_type="agent")
    def agent():
        return _anthropic().messages.create(
            model="claude-sonnet-4-5", max_tokens=10, messages=MESSAGES
        )

    agent()
    tracer.flush()
    llm = _only_llm_span(sink)
    parent = next(s for s in sink.spans if s["name"] == "agent")
    assert llm["parent_id"] == parent["id"] and llm["trace_id"] == parent["trace_id"]
