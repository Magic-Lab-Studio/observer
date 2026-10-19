"""Patching helpers shared by the provider instrumentors."""

from __future__ import annotations

import functools
import inspect
from typing import Any


def _unwrap(func: Any) -> Any:
    return getattr(func, "_llm_observatory_original", None) or func


def _as_method(patched: Any, original: Any) -> Any:
    """A plain function the class can bind: assigning a BOUND method as a class attribute lost
    the instance, so every instrumented call failed with "missing 1 required positional argument:
    'self'" (audit 2026-10-17, evaluation C1). A coroutine original gets an ``async def`` wrapper,
    so ``inspect.iscoroutinefunction`` and the SDK's own introspection see what they saw before.
    Idempotent: a method already patched by this SDK is returned to its original first."""
    original = _unwrap(original)

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def wrapper(inst: Any, *args: Any, **kwargs: Any) -> Any:
            return await patched(inst, *args, **kwargs)

    else:

        @functools.wraps(original)
        def wrapper(inst: Any, *args: Any, **kwargs: Any) -> Any:
            return patched(inst, *args, **kwargs)

    wrapper._llm_observatory_original = original  # type: ignore[attr-defined]
    wrapper._llm_observatory_patched = True  # type: ignore[attr-defined]
    return wrapper


def record_exception(span: Any, exc: BaseException) -> None:
    """Mark the span failed with the exception's type and message, then finish it."""
    from llm_observatory.tracer import SpanStatus

    span.set_status(SpanStatus.ERROR)
    span.set_attribute("error.type", type(exc).__name__)
    span.set_attribute("error.message", str(exc))
    span.end()
