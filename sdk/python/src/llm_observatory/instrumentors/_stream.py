"""Streamed responses (evaluation C1): the span ends when the stream does, not when it is returned.

``create(stream=True)`` returns a stream object at once; the text and the token usage arrive while
the caller iterates. The instrumentors hand back a proxy that yields every chunk unchanged, feeds it
to a provider-specific ``StreamAccumulator`` and ends the span:

* when the stream is exhausted: status ok, with the text, finish reasons, model and tokens seen;
* when iterating raises: status error, the exception re-raised unchanged;
* when the caller closes it or leaves its ``with`` block early, or drops it unconsumed: status
  unset and ``observer.stream.incomplete = true``, with whatever was seen so far.

Anything else (``response``, ``close``, ``until_done``...) is delegated to the provider's stream.
"""

from __future__ import annotations

from typing import Any

from llm_observatory.pricing import calculate_cost
from llm_observatory.tracer import Span, SpanStatus

MAX_TEXT = 100_000
_OWN = frozenset({"_stream", "_iter", "_rec"})


class StreamAccumulator:
    """What a stream said so far; ``add`` reads one provider chunk (never raises)."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.response_model: str | None = None
        self.response_id: str | None = None
        self.text: list[str] = []
        self.size = 0
        self.finish_reasons: list[str] = []
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None

    def add(self, chunk: Any) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def append_text(self, text: Any) -> None:
        if isinstance(text, str) and text and self.size < MAX_TEXT:
            self.text.append(text[: MAX_TEXT - self.size])
            self.size += len(text)

    def output(self) -> dict[str, Any]:
        return {"role": "assistant", "content": "".join(self.text)}


class OpenAIAccumulator(StreamAccumulator):
    def add(self, chunk: Any) -> None:
        model = getattr(chunk, "model", None)
        if isinstance(model, str):
            self.response_model = model
        cid = getattr(chunk, "id", None)
        if isinstance(cid, str):
            self.response_id = cid
        for choice in getattr(chunk, "choices", None) or []:
            delta = getattr(choice, "delta", None)
            self.append_text(getattr(delta, "content", None))
            reason = getattr(choice, "finish_reason", None)
            if isinstance(reason, str):
                self.finish_reasons.append(reason)
        usage = getattr(chunk, "usage", None)  # last chunk with stream_options.include_usage
        if usage is not None:
            self.input_tokens = getattr(usage, "prompt_tokens", None)
            self.output_tokens = getattr(usage, "completion_tokens", None)


class AnthropicAccumulator(StreamAccumulator):
    def add(self, event: Any) -> None:
        kind = getattr(event, "type", None)
        if kind == "message_start":
            message = getattr(event, "message", None)
            self.response_model = getattr(message, "model", None) or self.response_model
            self.response_id = getattr(message, "id", None) or self.response_id
            usage = getattr(message, "usage", None)
            if usage is not None:
                self.input_tokens = getattr(usage, "input_tokens", None)
                self.output_tokens = getattr(usage, "output_tokens", None)
        elif kind == "content_block_delta":
            self.append_text(getattr(getattr(event, "delta", None), "text", None))
        elif kind == "message_delta":
            reason = getattr(getattr(event, "delta", None), "stop_reason", None)
            if isinstance(reason, str):
                self.finish_reasons.append(reason)
            usage = getattr(event, "usage", None)
            out = getattr(usage, "output_tokens", None) if usage is not None else None
            if isinstance(out, int):
                self.output_tokens = out

    def output(self) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "".join(self.text)}]}


class _Recorder:
    """Ends the span exactly once, from what the accumulator saw."""

    def __init__(self, span: Span, acc: StreamAccumulator) -> None:
        self.span = span
        self.acc = acc
        self.done = False

    def add(self, chunk: Any) -> None:
        try:
            self.acc.add(chunk)
        except Exception as e:  # reading a chunk for telemetry must never break the stream
            self.span.set_attribute("observer.instrumentation_error", str(e)[:500])

    def finish(self, outcome: str, exc: BaseException | None = None) -> None:
        """``outcome``: ``complete`` | ``incomplete`` | ``error``."""
        if self.done:
            return
        self.done = True
        span, acc = self.span, self.acc
        try:
            span.set_attribute("gen_ai.response.streamed", True)
            if acc.text:
                span.set_output(acc.output())
            if acc.finish_reasons:
                span.set_attribute("gen_ai.response.finish_reasons", acc.finish_reasons)
            if acc.response_model:
                span.set_attribute("gen_ai.response.model", acc.response_model)
            if acc.response_id:
                span.set_attribute("response_id", acc.response_id)
            if isinstance(acc.input_tokens, int) or isinstance(acc.output_tokens, int):
                tokens_in = acc.input_tokens or 0
                tokens_out = acc.output_tokens or 0
                span.set_token_usage(tokens_in, tokens_out)
                span.set_attribute("gen_ai.usage.input_tokens", tokens_in)
                span.set_attribute("gen_ai.usage.output_tokens", tokens_out)
                cost = calculate_cost(acc.model, tokens_in, tokens_out)
                if cost is not None:
                    span.set_cost(cost)
        except Exception as e:
            span.set_attribute("observer.instrumentation_error", str(e)[:500])
        if outcome == "error" and exc is not None:
            span.set_status(SpanStatus.ERROR)
            span.set_attribute("error.type", type(exc).__name__)
            span.set_attribute("error.message", str(exc))
        elif outcome == "complete":
            span.set_status(SpanStatus.OK)
        else:
            span.set_attribute("observer.stream.incomplete", True)
            span.end(SpanStatus.UNSET)  # end() alone would call an unfinished stream ok
            return
        span.end()


class SyncStreamProxy:
    """Iterates the provider's stream unchanged and ends the span with it."""

    def __init__(self, stream: Any, span: Span, acc: StreamAccumulator) -> None:
        self._stream = stream
        self._iter: Any = None
        self._rec = _Recorder(span, acc)

    def __iter__(self) -> SyncStreamProxy:
        return self

    def __next__(self) -> Any:
        if self._iter is None:
            self._iter = iter(self._stream)
        try:
            chunk = next(self._iter)
        except StopIteration:
            self._rec.finish("complete")
            raise
        except BaseException as e:
            self._rec.finish("error", e)
            raise
        self._rec.add(chunk)
        return chunk

    def __enter__(self) -> SyncStreamProxy:
        enter = getattr(self._stream, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def __exit__(self, *exc: Any) -> Any:
        try:
            exit_ = getattr(self._stream, "__exit__", None)
            return exit_(*exc) if exit_ is not None else None
        finally:
            self._rec.finish("error" if exc[1] is not None else "incomplete", exc[1])

    def close(self) -> None:
        try:
            close = getattr(self._stream, "close", None)
            if close is not None:
                close()
        finally:
            self._rec.finish("incomplete")

    def __getattr__(self, name: str) -> Any:
        if name in _OWN:
            raise AttributeError(name)
        return getattr(self._stream, name)

    def __del__(self) -> None:
        try:
            self._rec.finish("incomplete")
        except Exception:
            pass


class AsyncStreamProxy:
    """The async twin of :class:`SyncStreamProxy`."""

    def __init__(self, stream: Any, span: Span, acc: StreamAccumulator) -> None:
        self._stream = stream
        self._iter: Any = None
        self._rec = _Recorder(span, acc)

    def __aiter__(self) -> AsyncStreamProxy:
        return self

    async def __anext__(self) -> Any:
        if self._iter is None:
            self._iter = self._stream.__aiter__()
        try:
            chunk = await self._iter.__anext__()
        except StopAsyncIteration:
            self._rec.finish("complete")
            raise
        except BaseException as e:
            self._rec.finish("error", e)
            raise
        self._rec.add(chunk)
        return chunk

    async def __aenter__(self) -> AsyncStreamProxy:
        enter = getattr(self._stream, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        try:
            exit_ = getattr(self._stream, "__aexit__", None)
            return (await exit_(*exc)) if exit_ is not None else None
        finally:
            self._rec.finish("error" if exc[1] is not None else "incomplete", exc[1])

    async def close(self) -> None:
        try:
            close = getattr(self._stream, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        finally:
            self._rec.finish("incomplete")

    def __getattr__(self, name: str) -> Any:
        if name in _OWN:
            raise AttributeError(name)
        return getattr(self._stream, name)

    def __del__(self) -> None:
        try:
            self._rec.finish("incomplete")
        except Exception:
            pass
