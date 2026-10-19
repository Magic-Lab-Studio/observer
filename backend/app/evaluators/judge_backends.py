"""Provider-neutral transport for the LLM judge (evaluation 2026-10-18, P4).

``OBSERVATORY_JUDGE_PROVIDER`` picks the backend:

* ``openai`` (default): OpenAI chat completions; key ``OBSERVATORY_JUDGE_API_KEY`` or
  ``OPENAI_API_KEY``.
* ``openai-compatible``: any endpoint that speaks the OpenAI chat API, local ones included
  (``OBSERVATORY_JUDGE_BASE_URL=http://127.0.0.1:11434/v1`` for Ollama); the key is optional.
* ``anthropic``: the official ``anthropic`` package (optional, ``pip install anthropic``);
  credentials as the SDK resolves them (``OBSERVATORY_JUDGE_API_KEY``, ``ANTHROPIC_API_KEY`` or a
  local profile).

The judge is off until one is configured: deterministic criteria never need it. A judge that
cannot answer raises ``JudgeUnavailableError`` with a reason and whether retrying may help; the
evaluator records that instead of scoring the criterion. Credentials are never logged. A judge
reads the trace content it is asked to grade, so a cloud judge sends it to that provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

PROVIDERS = ("openai", "openai-compatible", "anthropic")
_DEFAULT_MODEL = {"openai": "gpt-4o", "anthropic": "claude-opus-5-5"}
# Models that accept the server-side refusal fallback (Claude API).
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class JudgeUnavailableError(Exception):
    """The judge gave no usable answer."""

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass
class JudgeConfig:
    provider: str
    model: str | None
    api_key: str | None
    base_url: str | None

    @classmethod
    def from_env(cls, model: str | None = None, api_key: str | None = None) -> JudgeConfig:
        provider = (os.getenv("OBSERVATORY_JUDGE_PROVIDER") or "openai").strip().lower()
        key = api_key or os.getenv("OBSERVATORY_JUDGE_API_KEY") or None
        if provider == "openai":
            key = key or os.getenv("OPENAI_API_KEY") or None
        elif provider == "anthropic":
            key = key or os.getenv("ANTHROPIC_API_KEY") or None
        return cls(
            provider=provider,
            model=model or os.getenv("OBSERVATORY_JUDGE_MODEL") or _DEFAULT_MODEL.get(provider),
            api_key=key,
            base_url=(os.getenv("OBSERVATORY_JUDGE_BASE_URL") or "").strip() or None,
        )

    def problem(self) -> str | None:
        """Why this configuration cannot call a judge, or None."""
        if self.provider not in PROVIDERS:
            return f"judge_provider_unknown:{self.provider[:40]}"
        if not self.model:
            return "judge_model_not_set"
        if self.provider == "openai" and not self.api_key:
            return "judge_not_configured"
        if self.provider == "openai-compatible" and not self.base_url:
            return "judge_base_url_not_set"
        return None


def _status_retryable(status: int) -> bool:
    return status in (408, 409, 425, 429) or status >= 500


async def _openai_chat(config: JudgeConfig, system: str, user: str) -> str:
    import httpx

    base = (config.base_url or "https://api.openai.com/v1").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    body = {
        "model": config.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{base}/chat/completions", headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise JudgeUnavailableError("judge_timeout", retryable=True) from exc
    except httpx.HTTPError as exc:
        reason = f"judge_unreachable:{type(exc).__name__}"
        raise JudgeUnavailableError(reason, retryable=True) from exc
    if response.status_code >= 400:
        raise JudgeUnavailableError(
            f"judge_http_{response.status_code}", retryable=_status_retryable(response.status_code)
        )
    try:
        return str(response.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise JudgeUnavailableError("judge_malformed_response", retryable=False) from exc


async def _anthropic_message(config: JudgeConfig, system: str, user: str) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise JudgeUnavailableError("judge_anthropic_package_missing", retryable=False) from exc
    try:
        client = anthropic.AsyncAnthropic(api_key=config.api_key, max_retries=2)
    except Exception as exc:  # no credentials the SDK can resolve
        raise JudgeUnavailableError("judge_not_configured", retryable=False) from exc
    request: dict = {
        "model": config.model,
        "max_tokens": 4096,  # room for adaptive thinking before the one-line JSON answer
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if config.model in _FALLBACK_MODELS:
        # a policy decline is retried on a fallback model inside the same call
        request["betas"] = ["server-side-fallback-2026-07-01"]
        request["fallbacks"] = "default"
    try:
        async with client:
            message = await client.beta.messages.create(**request)
    except anthropic.RateLimitError as exc:
        raise JudgeUnavailableError("judge_http_429", retryable=True) from exc
    except anthropic.APIStatusError as exc:
        raise JudgeUnavailableError(
            f"judge_http_{exc.status_code}", retryable=_status_retryable(exc.status_code)
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise JudgeUnavailableError("judge_unreachable", retryable=True) from exc
    if message.stop_reason == "refusal":
        raise JudgeUnavailableError("judge_refused", retryable=False)
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise JudgeUnavailableError("judge_empty_response", retryable=True)
    return text


async def ask_judge(config: JudgeConfig, system: str, user: str) -> str:
    """The judge's raw answer, or ``JudgeUnavailableError``."""
    problem = config.problem()
    if problem:
        raise JudgeUnavailableError(problem, retryable=False)
    if config.provider == "anthropic":
        return await _anthropic_message(config, system, user)
    return await _openai_chat(config, system, user)
