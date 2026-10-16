"""Rate limiter shared across the application."""

from __future__ import annotations

import hmac
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth import API_KEY


def _rate_limit_key(request: Request) -> str:  # noqa: F821
    """Rate limit identity: the API key only when it is the real one, otherwise the client IP.

    Trusting any ``Authorization`` value let a client dodge the limit by sending a new fake token
    on every request (audit 2026-10-16), and with authentication off every value is "valid"."""
    if API_KEY:
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else auth
        if token and hmac.compare_digest(token.encode(), API_KEY.encode()):
            return "apikey:valid"
    return get_remote_address(request)


# Configurable via env vars
_default_limit = os.getenv("RATE_LIMIT_DEFAULT", "60/minute")
_batch_limit = os.getenv("RATE_LIMIT_BATCH", "10/minute")

limiter = Limiter(key_func=_rate_limit_key, default_limits=[_default_limit])
