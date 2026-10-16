"""Size limits for untrusted ingestion input (audit 2026-10-16: the generic routes had none).

Every limit is an environment variable so a deployment with very large prompts can raise it
deliberately. They apply before anything is stored or processed.
"""

from __future__ import annotations

import json
import os
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


MAX_BODY_BYTES = _int_env("OBSERVATORY_MAX_BODY_BYTES", 16 * 1024 * 1024, 1024)
MAX_JSON_FIELD_BYTES = _int_env("OBSERVATORY_MAX_JSON_FIELD_BYTES", 256 * 1024, 1024)
MAX_JSON_DEPTH = _int_env("OBSERVATORY_MAX_JSON_DEPTH", 12)
MAX_SPANS_PER_TRACE = _int_env("OBSERVATORY_MAX_SPANS_PER_TRACE", 500)
MAX_SPANS_PER_BATCH = _int_env("OBSERVATORY_MAX_SPANS_PER_BATCH", 1000)
MAX_TRACES_PER_IMPORT = _int_env("OBSERVATORY_MAX_TRACES_PER_IMPORT", 200)


def _depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_JSON_DEPTH:
        return depth
    if isinstance(value, dict):
        return max((_depth(v, depth + 1) for v in value.values()), default=depth)
    if isinstance(value, list):
        return max((_depth(v, depth + 1) for v in value), default=depth)
    return depth


def bounded_json(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reject a JSON field that is too large or too deeply nested (a pydantic validator body)."""
    if value is None:
        return None
    if _depth(value) > MAX_JSON_DEPTH:
        raise ValueError(f"JSON field exceeds the maximum depth of {MAX_JSON_DEPTH}")
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
    if len(encoded.encode()) > MAX_JSON_FIELD_BYTES:
        raise ValueError(f"JSON field exceeds {MAX_JSON_FIELD_BYTES} bytes")
    return value


class BodyLimitMiddleware:
    """Answer 413 to a request body over ``MAX_BODY_BYTES``, declared or streamed (no
    Content-Length is needed to hit it), before the application parses a byte of it."""

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes if max_bytes is not None else MAX_BODY_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return
        seen = 0
        rejected = False
        started = False

        async def counted() -> Message:
            nonlocal seen, rejected
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    rejected = True
                    # Stop the application reading more: it sees a disconnect.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, counted, guarded_send)
        if rejected and not started:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
