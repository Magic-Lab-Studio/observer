"""OpenAI auto-instrumentation."""

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


class OpenAIInstrumentor:
    """Instruments OpenAI API calls to capture traces."""

    def __init__(self) -> None:
        self._original_sync_create: Any | None = None
        self._original_async_create: Any | None = None

    def instrument(self) -> None:
        """Patch OpenAI to capture traces."""
        try:
            import openai

            # Patch sync completions
            self._original_sync_create = _unwrap(
                openai.resources.chat.completions.Completions.create
            )
            openai.resources.chat.completions.Completions.create = _as_method(
                self._patched_sync_create, self._original_sync_create
            )

            # Patch async completions
            self._original_async_create = _unwrap(
                openai.resources.chat.completions.AsyncCompletions.create
            )
            openai.resources.chat.completions.AsyncCompletions.create = _as_method(
                self._patched_async_create, self._original_async_create
            )
        except ImportError:
            pass

    def _build_span(
        self, model: str, messages: list[dict[str, Any]]
    ) -> tuple[Span, Any]:
        """Build a span for an OpenAI call."""
        tracer = get_tracer()
        span = tracer.start_span(
            name="openai.chat.completions.create",
            span_type="llm",
        )
        span.set_input({"model": model, "messages": messages})
        span.set_attribute("gen_ai.system", "openai")
        span.set_attribute("gen_ai.request.model", model)
        return span, tracer

    def _finish_span(self, span: Span, result: Any, model: str) -> None:
        """Extract data from result and finish span."""
        try:
            # Extract token usage
            if hasattr(result, "usage") and result.usage:
                input_tokens = getattr(result.usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(result.usage, "completion_tokens", 0) or 0
                span.set_token_usage(input_tokens, output_tokens)

                # Calculate cost
                cost = calculate_cost(model, input_tokens, output_tokens)
                if cost is not None:
                    span.set_cost(cost)

            # Extract output
            if hasattr(result, "choices") and result.choices:
                choice = result.choices[0]
                if hasattr(choice, "message"):
                    message = choice.message
                    output = {}
                    if hasattr(message, "content"):
                        output["content"] = message.content
                    if hasattr(message, "role"):
                        output["role"] = message.role
                    span.set_output(output)

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
            import openai

            if self._original_sync_create is not None:
                openai.resources.chat.completions.Completions.create = (
                    self._original_sync_create
                )
            if self._original_async_create is not None:
                openai.resources.chat.completions.AsyncCompletions.create = (
                    self._original_async_create
                )
        except ImportError:
            pass
