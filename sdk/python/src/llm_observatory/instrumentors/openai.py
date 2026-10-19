"""OpenAI auto-instrumentation."""

from __future__ import annotations

from typing import Any

from llm_observatory.instrumentors._patching import _as_method, _unwrap, record_exception
from llm_observatory.pricing import calculate_cost
from llm_observatory.tracer import Span, SpanStatus, get_tracer


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
        span.set_attribute("gen_ai.provider.name", "openai")
        span.set_attribute("gen_ai.operation.name", "chat")
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
            response_model = getattr(result, "model", None)
            if isinstance(response_model, str):
                span.set_attribute("gen_ai.response.model", response_model)
            if span.tokens is not None:
                span.set_attribute("gen_ai.usage.input_tokens", span.tokens.input)
                span.set_attribute("gen_ai.usage.output_tokens", span.tokens.output)
            span.set_status(SpanStatus.OK)

        except Exception as e:
            # The call succeeded; only reading its result for telemetry failed.
            span.set_status(SpanStatus.OK)
            span.set_attribute("observer.instrumentation_error", str(e)[:500])

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
        except BaseException as e:
            record_exception(span, e)
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
        except BaseException as e:
            record_exception(span, e)
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
