"""Authentication middleware for API key validation."""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Security, WebSocket
from fastapi.security import APIKeyHeader

# Read API key from environment. If not set, auth is disabled (dev mode).
API_KEY = os.getenv("OBSERVATORY_API_KEY", "")

_api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


async def require_api_key(
    authorization: str | None = Security(_api_key_header),
) -> None:
    """Dependency that validates the API key when authentication is enabled.

    If OBSERVATORY_API_KEY is not set, all requests are allowed (dev mode).
    If set, requests must include `Authorization: Bearer <key>`.
    """
    if not API_KEY:
        return

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing API key")

    # Support both "Bearer <key>" and raw key formats
    token = authorization
    if authorization.lower().startswith("bearer "):
        token = authorization[7:]

    if not hmac.compare_digest(token.encode(), API_KEY.encode()):
        raise HTTPException(status_code=403, detail="Invalid API key")


def websocket_authorized(websocket: WebSocket) -> bool:
    """Same rule as require_api_key for /ws/live, which had no check at all (audit 2026-10-14).

    The key comes in the Authorization header (the dashboard's proxy adds it) or, for
    scripts, ``?api_key=``.
    """
    if not API_KEY:
        return True
    token = websocket.headers.get("authorization") or websocket.query_params.get("api_key") or ""
    if token.lower().startswith("bearer "):
        token = token[7:]
    return bool(token) and hmac.compare_digest(token.encode(), API_KEY.encode())
