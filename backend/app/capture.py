"""What Observer stores from a span: per-project capture modes.

* ``metadata``: no ``input``/``output``; attributes whose key looks like content are dropped.
* ``redacted``: everything is kept but emails, phone numbers, card and ID numbers, and tokens/keys
  are masked, and values under secret-looking keys are replaced. Best-effort patterns: names,
  addresses and free-form secrets are **not** detected.
* ``full``: stored as sent.

A project's mode is set through ``/v1/privacy/projects/{id}``. Without one, each ingestion path
keeps its historical behaviour (the SDK and the Manitos exporter store what they send; OTLP is
``metadata``); ``OBSERVATORY_CAPTURE_DEFAULT`` overrides those defaults for every path.
"""

from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import ProjectSetting

MODES = ("metadata", "redacted", "full")

# Standard attributes kept in metadata mode even though their name looks content-like.
ALLOWED_ATTRIBUTES = (
    "gen_ai.usage.",
    "gen_ai.operation.name",
    "gen_ai.system",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.response.finish_reasons",
    "gen_ai.tool.name",
    "gen_ai.agent.name",
    "gen_ai.request.temperature",
    "gen_ai.request.max_tokens",
)
_DENY = re.compile(
    r"prompt|completion|content|message|input|output|query|statement|body|text|payload|"
    r"password|passwd|token|secret|credential|authorization|cookie|api[._-]?key|email|"
    r"phone|address|url|path|file|argument|param",
    re.IGNORECASE,
)
_SECRET_KEY = re.compile(
    r"password|passwd|secret|token|authorization|cookie|api[._-]?key|credential", re.IGNORECASE
)
_MAX_ATTR = 200

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    (
        "key",
        re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[abprs]|AKIA|ASIA|AIza)[-_A-Za-z0-9]{10,}\b"),
    ),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card", re.compile(r"(?<![\d-])(?:\d[ -]?){13,19}(?![\d-])")),
    (
        "phone",
        re.compile(r"(?<![\w.])(?:\+\d[\d\s().-]{8,}\d|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})(?![\w])"),
    ),
)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0


def redact_text(text: str) -> str:
    out = text
    for label, pattern in _PATTERNS:
        if label == "card":
            out = pattern.sub(
                lambda m: "[REDACTED:card]" if _luhn(re.sub(r"\D", "", m.group())) else m.group(),
                out,
            )
        else:
            out = pattern.sub(f"[REDACTED:{label}]", out)
    return out


def redact_json(value: Any, key: str = "") -> Any:
    """Mask sensitive patterns in every string of a JSON-like value."""
    if key and _SECRET_KEY.search(key) and value not in (None, "", False):
        return "[REDACTED:secret]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_json(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_json(v) for v in value]
    return value


def filter_attributes(attrs: dict[str, Any] | None, mode: str) -> dict[str, Any] | None:
    """Metadata mode: drop content-like keys and cut long strings; the other modes keep them."""
    if attrs is None or mode != "metadata":
        return attrs
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        if not key.startswith(ALLOWED_ATTRIBUTES) and _DENY.search(key):
            continue
        if isinstance(value, str):
            value = value[:_MAX_ATTR]
        elif isinstance(value, list):
            value = [v[:_MAX_ATTR] if isinstance(v, str) else v for v in value[:16]]
        out[key] = value
    return out


def apply_capture(
    mode: str,
    *,
    inp: Any = None,
    out: Any = None,
    attributes: dict[str, Any] | None = None,
    meta: Any = None,
) -> tuple[Any, Any, dict[str, Any] | None, Any]:
    """``(input, output, attributes, metadata)`` as they may be stored under ``mode``."""
    if mode == "metadata":
        # the free-form metadata dict (64 KiB in the Manitos envelope) was stored whole in
        # metadata mode (audit 2026-10-17)
        return None, None, filter_attributes(attributes, mode), redact_json(meta)
    if mode == "redacted":
        return (
            redact_json(inp),
            redact_json(out),
            redact_json(attributes) if attributes is not None else None,
            redact_json(meta),
        )
    return inp, out, attributes, meta


def default_mode(path_default: str) -> str:
    override = os.getenv("OBSERVATORY_CAPTURE_DEFAULT", "").strip().lower()
    return override if override in MODES else path_default


async def capture_mode(session: AsyncSession, project_id: str | None, path_default: str) -> str:
    """The project's configured mode, or the default of the ingestion path."""
    if project_id:
        row = (
            await session.execute(
                select(ProjectSetting.capture_mode).where(ProjectSetting.project_id == project_id)
            )
        ).scalar_one_or_none()
        if row in MODES:
            return str(row)
    return default_mode(path_default)


def content_like_keys(attrs: dict[str, Any] | None) -> list[str]:
    """Attribute keys that metadata mode would drop (for the privacy report)."""
    return [k for k in (attrs or {}) if not k.startswith(ALLOWED_ATTRIBUTES) and _DENY.search(k)]


def has_sensitive_patterns(*values: Any) -> bool:
    """True when redaction would change any of the values (an email, a key, a card...)."""
    return any(v is not None and redact_json(v) != v for v in values)
