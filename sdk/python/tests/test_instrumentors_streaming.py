"""C1 of the 2026-10-18 evaluation: a streamed call's span ends when the stream does.

Real OpenAI and Anthropic clients with a mocked SSE transport (no key, no network). Before the fix
the span ended when ``create(stream=True)`` returned, so it had neither the text nor the tokens.
"""

from __future__ import annotations

import asyncio
import gc
import json

import pytest

try:  # current provider SDKs ship their own fork of httpx and refuse a plain httpx client
    import httpx2 as httpx
except ImportError:
    import httpx

from llm_observatory.instrumentors.anthropic import AnthropicInstrumentor
from llm_observatory.instrumentors.openai import OpenAIInstrumentor
from llm_observatory.tracer import Tracer, set_tracer

anthropic = pytest.importorskip("anthropic")
openai = pytest.importorskip("openai")

MESSAGES = [{"role": "user", "content": "hi"}]


def _openai_sse(usage: bool = True) -> bytes:
    def chunk(delta: dict, finish: str | None = None, **extra) -> dict:
        return {"id": "chatcmpl-9", "object": "chat.completion.chunk", "created": 1,
                "model": "gpt-4o-mini-2024-07-18",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}

    events = [chunk({"role": "assistant", "content": ""}), chunk({"content": "ho"}),
              chunk({"content": "la"}), chunk({}, "stop")]
    if usage:
        events.append({"id": "chatcmpl-9", "object": "chat.completion.chunk", "created": 1,
                       "model": "gpt-4o-mini-2024-07-18", "choices": [],
                       "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}})
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return body.encode()


def _anthropic_sse() -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_9", "type": "message", "role": "assistant", "content": [],
            "model": "claude-sonnet-4-5-20250929", "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "ho"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "la"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 3}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {k}\ndata: {json.dumps(v)}\n\n" for k, v in events).encode()


def _sse(body: bytes):
    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return handler


def _async_sse(body: bytes):
    async def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return handler


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


@pytest.fixture
def instrumented():
    a, o = AnthropicInstrumentor(), OpenAIInstrumentor()
    a.instrument()
    o.instrument()
    yield
    a.uninstrument()
    o.uninstrument()


def _llm(tracer: Tracer, sink: Capture) -> dict:
    tracer.flush()
    spans = [s for s in sink.spans if s["span_type"] == "llm"]
    assert len(spans) == 1, spans
    return spans[0]


def _openai_client(body: bytes = _openai_sse()):
    return openai.OpenAI(api_key="test", max_retries=0,
                         http_client=httpx.Client(transport=httpx.MockTransport(_sse(body))))


def _anthropic_client():
    transport = httpx.MockTransport(_sse(_anthropic_sse()))
    return anthropic.Anthropic(api_key="test", max_retries=0,
                               http_client=httpx.Client(transport=transport))


def test_openai_stream_span_has_the_text_tokens_and_ends_after_the_last_chunk(
    capture, instrumented
):
    tracer, sink = capture
    stream = _openai_client().chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True,
        stream_options={"include_usage": True})
    tracer.flush()
    assert [s for s in sink.spans if s["span_type"] == "llm"] == []  # not ended on return
    text = "".join(c.choices[0].delta.content or "" for c in stream if c.choices)
    assert text == "hola"
    span = _llm(tracer, sink)
    assert span["status"] == "ok" and span["end_time"] is not None
    assert span["output"] == {"role": "assistant", "content": "hola"}
    assert (span["tokens_input"], span["tokens_output"]) == (7, 2)
    assert span["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]
    assert span["attributes"]["gen_ai.response.model"] == "gpt-4o-mini-2024-07-18"
    assert span["attributes"]["gen_ai.response.streamed"] is True


def test_openai_stream_as_a_context_manager_left_early_is_incomplete(capture, instrumented):
    tracer, sink = capture
    with _openai_client().chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    ) as stream:
        next(iter(stream))
        next(stream)
    span = _llm(tracer, sink)
    assert span["status"] == "unset" and span["end_time"] is not None
    assert span["attributes"]["observer.stream.incomplete"] is True
    assert span["output"] == {"role": "assistant", "content": "ho"}


def test_an_abandoned_stream_still_ends_its_span(capture, instrumented):
    tracer, sink = capture
    stream = _openai_client().chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True)
    del stream
    gc.collect()
    span = _llm(tracer, sink)
    assert span["attributes"]["observer.stream.incomplete"] is True


def test_openai_async_stream(capture, instrumented):
    tracer, sink = capture

    async def run() -> str:
        client = openai.AsyncOpenAI(
            api_key="test", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(
                _async_sse(_openai_sse()))))
        stream = await client.chat.completions.create(
            model="gpt-4o-mini", messages=MESSAGES, stream=True)
        return "".join([c.choices[0].delta.content or "" async for c in stream if c.choices])

    assert asyncio.run(run()) == "hola"
    span = _llm(tracer, sink)
    assert span["status"] == "ok" and span["output"]["content"] == "hola"


def test_anthropic_stream_span_has_the_text_tokens_and_stop_reason(capture, instrumented):
    tracer, sink = capture
    events = list(_anthropic_client().messages.create(
        model="claude-sonnet-4-5", max_tokens=10, messages=MESSAGES, stream=True))
    assert events[-1].type == "message_stop"
    span = _llm(tracer, sink)
    assert span["status"] == "ok"
    assert span["output"] == {"content": [{"type": "text", "text": "hola"}]}
    assert (span["tokens_input"], span["tokens_output"]) == (12, 3)
    assert span["attributes"]["gen_ai.response.finish_reasons"] == ["end_turn"]
    assert span["cost_usd"] is not None


def test_anthropic_async_stream(capture, instrumented):
    tracer, sink = capture

    async def run() -> int:
        client = anthropic.AsyncAnthropic(
            api_key="test", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(
                _async_sse(_anthropic_sse()))))
        stream = await client.messages.create(
            model="claude-sonnet-4-5", max_tokens=10, messages=MESSAGES, stream=True)
        return len([e async for e in stream])

    assert asyncio.run(run()) == 7
    span = _llm(tracer, sink)
    assert span["status"] == "ok" and span["tokens_output"] == 3


def test_a_stream_that_fails_midway_is_an_error_and_the_error_reaches_the_caller(
    capture, instrumented
):
    tracer, sink = capture
    body = _openai_sse().split(b"data: [DONE]")[0] + b'data: {"error": {"message": "boom"}}\n\n'
    stream = _openai_client(body).chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True)
    with pytest.raises(openai.APIError):
        list(stream)
    span = _llm(tracer, sink)
    assert span["status"] == "error" and "boom" in json.dumps(span["attributes"])
