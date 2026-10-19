"""Anthropic auto-instrumentation."""

from __future__ import annotations

from typing import Any

from llm_observatory.pricing import calculate_cost
from llm_observatory.tracer import Span, SpanStatus, get_tracer


def _unwrap(func):
    return getattr(func, "_llm_observatory_original", None) or func


def _as_method(patched, original):
    """A plain function the class can bind: assigning a BOUND method as a class attribute lost
    the instance, so every
    instrumented call failed with "missing 1 required positional argument: 'self'"
    (audit 2026-10-17). Idempotent:
    a class already patched by this SDK is returned to its original before being patched again."""
    if getattr(original, "_llm_observatory_original", None) is not None:
        original = original._llm_observatory_original

    def wrapper(inst, *args, **kwargs):
        return patched(inst, *args, **kwargs)

    wrapper._llm_observatory_original = original
    wrapper._llm_observatory_patched = True
    return wrapper


class AnthropicInstrumentor:
    """Instruments Anthropic API calls to capture traces."""

    def __init__(self) -> None:
        self._original_sync_create: Any | None = None
        self._original_async_create: Any | None = None

    def instrument(self) -> None:
        """Patch Anthropic to capture traces."""
        try:
            import anthropic

            # Patch sync messages
            self._original_sync_create = _unwrap(anthropic.resources.messages.Messages.create)
            anthropic.resources.messages.Messages.create = _as_method(
                self._patched_sync_create, self._original_sync_create
            )

            # Patch async messages
            self._original_async_create = _unwrap(
                anthropic.resources.messages.AsyncMessages.create
            )
            anthropic.resources.messages.AsyncMessages.create = _as_method(
                self._patched_async_create, self._original_async_create
            )
        except ImportError:
            pass

    def _build_span(
        self, model: str, messages: list[dict[str, Any]]
    ) -> tuple[Span, Any]:
        """Build a span for an Anthropic call."""
        tracer = get_tracer()
        span = tracer.start_span(
            name="anthropic.messages.create",
            span_type="llm",
        )
        span.set_input({"model": model, "messages": messages})
        span.set_attribute("gen_ai.system", "anthropic")
        span.set_attribute("gen_ai.request.model", model)
        return span, tracer

    def _finish_span(self, span: Span, result: Any, model: str) -> None:
        """Extract data from result and finish span."""
        try:
            # Extract token usage
            if hasattr(result, "usage"):
                input_tokens = getattr(result.usage, "input_tokens", 0) or 0
                output_tokens = getattr(result.usage, "output_tokens", 0) or 0
                span.set_token_usage(input_tokens, output_tokens)

                # Calculate cost
                cost = calculate_cost(model, input_tokens, output_tokens)
                if cost is not None:
                    span.set_cost(cost)

            # Extract output
            if hasattr(result, "content") and result.content:
                content_blocks = []
                for block in result.content:
                    block_data = {}
                    if hasattr(block, "type"):
                        block_data["type"] = block.type
                    if hasattr(block, "text"):
                        block_data["text"] = block.text
                    content_blocks.append(block_data)
                span.set_output({"content": content_blocks})

            span.set_attribute("response_id", getattr(result, "id", None))
            span.set_status(SpanStatus.OK)

        except Exception as e:
            span.set_status(SpanStatus.ERROR)
            span.set_attribute("error.message", str(e))

        finally:
            span.end()

    def _patched_sync_create(self, inst: Any, *args: Any, **kwargs: Any) -> Any:
        """Patched sync create method."""
        model = kwargs.get("model", "unknown")
        messages = kwargs.get("messages", [])

        span, _ = self._build_span(model, messages)

        try:
            result = self._original_sync_create(inst, *args, **kwargs)
            self._finish_span(span, result, model)
            return result
        except Exception as e:
            span.set_status(SpanStatus.ERROR)
            span.set_attribute("error.message", str(e))
            span.end()
            raise

    async def _patched_async_create(self, inst: Any, *args: Any, **kwargs: Any) -> Any:
        """Patched async create method."""
        model = kwargs.get("model", "unknown")
        messages = kwargs.get("messages", [])

        span, _ = self._build_span(model, messages)

        try:
            result = await self._original_async_create(inst, *args, **kwargs)
            self._finish_span(span, result, model)
            return result
        except Exception as e:
            span.set_status(SpanStatus.ERROR)
            span.set_attribute("error.message", str(e))
            span.end()
            raise

    def uninstrument(self) -> None:
        """Remove instrumentation."""
        try:
            import anthropic

            if self._original_sync_create is not None:
                anthropic.resources.messages.Messages.create = (
                    self._original_sync_create
                )
            if self._original_async_create is not None:
                anthropic.resources.messages.AsyncMessages.create = (
                    self._original_async_create
                )
        except ImportError:
            pass
