"""HTTP exporter that sends spans to the Observer backend, built not to lose telemetry.

* Retries only what can succeed later (network errors, 408/425/429/5xx), with exponential backoff
  and jitter, honouring ``Retry-After``. Other 4xx answers (a malformed batch) are not retried: they
  never will succeed, and they are counted as ``rejected``.
* Optional **persistent spool** (``spool_dir``): a batch that still fails is written to disk and
  sent later, oldest first, on the next successful export or after a restart. Off by default. The
  spool holds spans as the tracer produced them (inputs and outputs included, unencrypted) with
  file mode 0600, and is bounded by ``spool_max_bytes`` (the oldest files are evicted first).
* ``stats()`` reports what happened: ``exported``, ``retried``, ``rejected``, ``dropped``,
  ``spooled``, ``recovered``, ``spool_evicted`` and the spans waiting in the spool (``pending``).
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from llm_observatory.exporters import BaseExporter

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_STAT_KEYS = ("exported", "retried", "rejected", "dropped", "spooled", "recovered", "spool_evicted")


def backoff_delay(attempt: int, base: float, cap: float, retry_after: float | None = None) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based): exponential, jittered, capped."""
    delay = min(cap, base * (2**attempt))
    delay = random.uniform(delay / 2, delay)  # noqa: S311 - jitter, not security
    if retry_after is not None:
        delay = max(delay, min(retry_after, cap))
    return delay


def _retry_after(response: httpx.Response | None) -> float | None:
    raw = getattr(response, "headers", {}).get("retry-after") if response is not None else None
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


class OTLPExporter(BaseExporter):
    """Exports spans to the Observer backend (``/v1/traces/batch``) via HTTP."""

    def __init__(
        self,
        endpoint: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        *,
        backoff_base: float = 0.5,
        backoff_max: float = 10.0,
        spool_dir: str | os.PathLike[str] | None = None,
        spool_max_bytes: int = 50 * 1024 * 1024,
        sleep: Any = time.sleep,
    ) -> None:
        """Initialize the exporter.

        Args:
            endpoint: Backend endpoint URL.
            api_key: Optional API key for authentication.
            timeout: Request timeout in seconds.
            max_retries: Attempts per batch before it is spooled or dropped.
            backoff_base: First retry delay in seconds (doubles each retry, with jitter).
            backoff_max: Longest wait between retries.
            spool_dir: Directory for the persistent spool; ``None`` disables it.
            spool_max_bytes: Disk budget of the spool; the oldest batches are evicted beyond it.
            sleep: Waiting function (tests replace it).
        """
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.spool_max_bytes = spool_max_bytes
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout)
        self._lock = threading.Lock()
        self._stats = dict.fromkeys(_STAT_KEYS, 0)
        self._spool = Path(spool_dir) if spool_dir else None
        if self._spool is not None:
            self._spool.mkdir(parents=True, exist_ok=True)

    # ── public API ────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        """Counters since start plus ``pending``: spans waiting in the spool."""
        with self._lock:
            out = dict(self._stats)
        out["pending"] = self._pending_spans()
        return out

    def export(self, spans: list[dict[str, Any]]) -> None:
        """Send ``spans``; a batch that cannot be sent is spooled (if enabled) or dropped."""
        if not spans:
            return
        outcome = self._send(spans)
        if outcome == "ok":
            self._count("exported", len(spans))
            self._drain_spool()
        elif outcome == "rejected":
            self._count("rejected", len(spans))
            logger.error("Backend rejected %d spans; they were not retried", len(spans))
        else:  # retryable failure that outlasted the attempts
            self._keep(spans)

    def flush(self) -> None:
        """Try the spool once more, then release the connection."""
        try:
            self._drain_spool()
        finally:
            self._client.close()

    # ── sending ───────────────────────────────────────────────────────────────

    def _send(self, spans: list[dict[str, Any]]) -> str:
        """``ok`` | ``rejected`` (permanent) | ``failed`` (retryable, attempts used up)."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(self.max_retries):
            retry_after = None
            try:
                response = self._client.post(
                    f"{self.endpoint}/v1/traces/batch", json={"spans": spans}, headers=headers
                )
                response.raise_for_status()
                logger.debug("Exported %d spans successfully", len(spans))
                return "ok"
            except httpx.HTTPStatusError as exc:
                status = getattr(exc.response, "status_code", 0)
                logger.warning(
                    "Export failed (attempt %d/%d): HTTP %s", attempt + 1, self.max_retries, status
                )
                if status not in _RETRYABLE_STATUS:
                    return "rejected"
                retry_after = _retry_after(exc.response)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Export failed (attempt %d/%d): %s", attempt + 1, self.max_retries, exc
                )
            if attempt < self.max_retries - 1:
                self._count("retried")
                self._sleep(
                    backoff_delay(attempt, self.backoff_base, self.backoff_max, retry_after)
                )
        return "failed"

    # ── spool ─────────────────────────────────────────────────────────────────

    def _count(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._stats[key] += n

    def _files(self) -> list[Path]:
        if self._spool is None:
            return []
        return sorted(self._spool.glob("batch-*.json"))  # names sort by time

    def _pending_spans(self) -> int:
        total = 0
        for path in self._files():
            try:
                total += len(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return total

    def _keep(self, spans: list[dict[str, Any]]) -> None:
        if self._spool is None:
            self._count("dropped", len(spans))
            logger.error(
                "Export failed after %d attempts, dropping %d spans", self.max_retries, len(spans)
            )
            return
        name = f"batch-{time.time_ns():020d}-{uuid.uuid4().hex[:8]}.json"
        path = self._spool / name
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(spans, handle, default=str)
        except (OSError, TypeError, ValueError):
            self._count("dropped", len(spans))
            logger.error("Could not spool %d spans; dropped", len(spans), exc_info=True)
            path.unlink(missing_ok=True)
            return
        self._count("spooled", len(spans))
        self._evict()

    def _evict(self) -> None:
        files = self._files()
        sizes = {p: p.stat().st_size for p in files if p.exists()}
        total = sum(sizes.values())
        for path in files:  # oldest first
            if total <= self.spool_max_bytes or len(sizes) <= 1:
                break
            try:
                dropped = len(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                dropped = 0
            total -= sizes.pop(path, 0)
            path.unlink(missing_ok=True)
            self._count("spool_evicted", dropped)
            logger.error("Spool over budget: evicted %d spans", dropped)

    def _drain_spool(self) -> None:
        """Send spooled batches oldest first; stop at the first one that still fails."""
        for path in self._files():
            try:
                spans = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                path.unlink(missing_ok=True)  # unreadable: nothing recoverable in it
                continue
            outcome = self._send(spans)
            if outcome == "failed":
                return
            path.unlink(missing_ok=True)
            self._count("recovered" if outcome == "ok" else "rejected", len(spans))

    def __del__(self) -> None:
        """Clean up the client."""
        try:
            self._client.close()
        except Exception:
            pass
