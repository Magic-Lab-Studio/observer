"""Passive, metadata-only integration validation for compatible agent runtimes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

_QUALITY_COUNT_KEYS = (
    "error_count",
    "degraded_count",
    "truncated_count",
    "tool_error_count",
    "fallback_count",
    "tts_error_count",
)
_EXPORTER_STAT_KEYS = (
    "queued",
    "accepted",
    "duplicates",
    "retried",
    "failed",
    "dropped",
    "spooled",
    "recovered",
    "spool_evicted",
    "circuit_opened",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value: Any, *, maximum: int = 160) -> str:
    return str(value or "").strip()[:maximum]


def _safe_url(value: str) -> str:
    """Remove credentials, query strings, and fragments from report metadata."""
    parsed = urlsplit(str(value or ""))
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _non_negative_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, result) if result == result else 0.0


@dataclass(frozen=True)
class PassiveGateThresholds:
    minimum_turns: int = 20
    minimum_availability_rate: float = 0.99
    maximum_error_rate: float = 0.05
    maximum_degraded_rate: float = 0.15
    maximum_truncated_rate: float = 0.05
    maximum_tool_error_rate: float = 0.10
    maximum_fallback_rate: float = 0.25
    maximum_tts_error_rate: float = 0.10
    maximum_average_duration_ms: float = 60_000.0
    maximum_persisted_pending: int = 0
    require_durable_delivery: bool = True


@dataclass(frozen=True)
class PassiveGateConfig:
    observer_url: str = "http://127.0.0.1:8000"
    manitos_ready_url: str = os.getenv("MANITOS_READY_URL", "")
    api_key: str = field(default="", repr=False)
    project_id: str = "manitos"
    environment: str | None = None
    analytics_hours: int = 720
    duration_seconds: float = 86_400.0
    interval_seconds: float = 60.0
    request_timeout_seconds: float = 5.0
    output_path: str = ".observer-state/manitos-phase8.json"
    thresholds: PassiveGateThresholds = field(default_factory=PassiveGateThresholds)

    def __post_init__(self) -> None:
        object.__setattr__(self, "observer_url", self.observer_url.rstrip("/"))
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        if self.interval_seconds < 0.1:
            raise ValueError("interval_seconds must be at least 0.1")
        if not 1 <= self.analytics_hours <= 720:
            raise ValueError("analytics_hours must be between 1 and 720")


def _safe_quality(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    safe: dict[str, Any] = {
        "project_id": _bounded_text(source.get("project_id"), maximum=128),
        "environment": _bounded_text(source.get("environment"), maximum=64) or None,
        "hours": _non_negative_int(source.get("hours")),
        "window_start": _bounded_text(source.get("window_start"), maximum=64) or None,
        "window_end": _bounded_text(source.get("window_end"), maximum=64) or None,
        "total_turns": _non_negative_int(source.get("total_turns")),
        "avg_duration_ms": _non_negative_float(source.get("avg_duration_ms")),
        "avg_ttft_ms": _non_negative_float(source.get("avg_ttft_ms")),
    }
    for key in _QUALITY_COUNT_KEYS:
        safe[key] = _non_negative_int(source.get(key))
    for key in ("models", "languages"):
        values: list[dict[str, Any]] = []
        for item in source.get(key) or []:
            if not isinstance(item, Mapping):
                continue
            values.append(
                {
                    "key": _bounded_text(item.get("key")),
                    "count": _non_negative_int(item.get("count")),
                }
            )
        safe[key] = values[:100]
    return safe


def _safe_exporter(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = payload or {}
    stats_source = source.get("stats") if isinstance(source.get("stats"), Mapping) else {}
    return {
        "enabled": source.get("enabled") is True,
        "privacy_mode": _bounded_text(source.get("privacy_mode"), maximum=32),
        "durable_delivery": source.get("durable_delivery") is True,
        "circuit_state": _bounded_text(source.get("circuit_state"), maximum=32),
        "in_memory_queued": _non_negative_int(source.get("in_memory_queued")),
        "persisted_pending": _non_negative_int(source.get("persisted_pending")),
        "spool_error": _bounded_text(source.get("spool_error"), maximum=80) or None,
        "stats": {key: _non_negative_int(stats_source.get(key)) for key in _EXPORTER_STAT_KEYS},
    }


def _extract_exporter(ready_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    features = (ready_payload or {}).get("features")
    if not isinstance(features, Mapping):
        return _safe_exporter(None)
    exporter = features.get("observer_exporter")
    return _safe_exporter(exporter if isinstance(exporter, Mapping) else None)


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> tuple[bool, int, dict[str, Any], str | None]:
    try:
        response = await client.get(url, headers=headers)
        status_code = int(response.status_code)
        if status_code != 200:
            return False, status_code, {}, f"http_{status_code}"
        payload = response.json()
        if not isinstance(payload, dict):
            return False, status_code, {}, "invalid_json_shape"
        return True, status_code, payload, None
    except (httpx.HTTPError, ValueError) as exc:
        return False, 0, {}, type(exc).__name__


async def collect_sample(
    client: httpx.AsyncClient,
    config: PassiveGateConfig,
    *,
    window_start: str | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    query: dict[str, str | int] = {
        "hours": config.analytics_hours,
        "project_id": config.project_id,
    }
    if window_start:
        query["since"] = window_start
    if config.environment:
        query["environment"] = config.environment
    health_ok, health_code, health, health_error = await _get_json(
        client, f"{config.observer_url}/health"
    )
    quality_ok, quality_code, quality, quality_error = await _get_json(
        client,
        f"{config.observer_url}/v1/analytics/manitos-quality?{urlencode(query)}",
        headers=headers,
    )
    ready_ok = True
    ready_code = 0
    ready_error: str | None = None
    ready: dict[str, Any] = {}
    if config.manitos_ready_url:
        ready_ok, ready_code, ready, ready_error = await _get_json(
            client, config.manitos_ready_url
        )
    else:
        ready_ok = False
        ready_code = 0
        ready_error = "readiness_url_not_configured"
    exporter = _extract_exporter(ready)
    observer_healthy = (
        health_ok
        and _bounded_text(health.get("status"), maximum=32) == "healthy"
        and _bounded_text(health.get("db"), maximum=32) == "ok"
        and quality_ok
    )
    return {
        "sampled_at": _utc_now(),
        "observer": {
            "ok": observer_healthy,
            "health_status_code": health_code,
            "quality_status_code": quality_code,
            "health_error": health_error,
            "quality_error": quality_error,
        },
        "manitos": {
            "ok": ready_ok,
            "status_code": ready_code,
            "error": ready_error,
            "exporter": exporter,
        },
        "quality": _safe_quality(quality),
    }


def _counter_delta(final: Mapping[str, Any], baseline: Mapping[str, Any], key: str) -> int:
    return max(0, _non_negative_int(final.get(key)) - _non_negative_int(baseline.get(key)))


def evaluate_samples(
    samples: list[dict[str, Any]],
    thresholds: PassiveGateThresholds,
) -> dict[str, Any]:
    observer_samples = [sample for sample in samples if sample.get("observer", {}).get("ok")]
    availability_rate = len(observer_samples) / len(samples) if samples else 0.0
    final = observer_samples[-1].get("quality", {}) if observer_samples else {}
    observed_turns = _non_negative_int(final.get("total_turns"))
    window_counts = {key: _non_negative_int(final.get(key)) for key in _QUALITY_COUNT_KEYS}

    def rate(key: str) -> float:
        return window_counts[key] / observed_turns if observed_turns else 0.0

    rates = {
        "error_rate": rate("error_count"),
        "degraded_rate": rate("degraded_count"),
        "truncated_rate": rate("truncated_count"),
        "tool_error_rate": rate("tool_error_count"),
        "fallback_rate": rate("fallback_count"),
        "tts_error_rate": rate("tts_error_count"),
    }
    manitos_samples = [sample for sample in samples if sample.get("manitos", {}).get("ok")]
    manitos_availability_rate = len(manitos_samples) / len(samples) if samples else 0.0
    exporter_baseline = (
        manitos_samples[0].get("manitos", {}).get("exporter", {}) if manitos_samples else {}
    )
    exporter_final = (
        manitos_samples[-1].get("manitos", {}).get("exporter", {}) if manitos_samples else {}
    )
    baseline_stats = exporter_baseline.get("stats", {})
    final_stats = exporter_final.get("stats", {})
    exporter_deltas = {
        key: _counter_delta(final_stats, baseline_stats, key) for key in _EXPORTER_STAT_KEYS
    }
    circuit_open_samples = sum(
        1
        for sample in manitos_samples
        if sample.get("manitos", {}).get("exporter", {}).get("circuit_state") == "open"
    )

    failures: list[str] = []
    if availability_rate < thresholds.minimum_availability_rate:
        failures.append("observer_availability_below_threshold")
    if manitos_availability_rate < thresholds.minimum_availability_rate:
        failures.append("manitos_availability_below_threshold")
    if samples and not samples[-1].get("manitos", {}).get("ok"):
        failures.append("manitos_not_ready_at_end")
    if observed_turns < thresholds.minimum_turns:
        failures.append("insufficient_observed_turns")
    for metric, maximum in (
        ("error_rate", thresholds.maximum_error_rate),
        ("degraded_rate", thresholds.maximum_degraded_rate),
        ("truncated_rate", thresholds.maximum_truncated_rate),
        ("tool_error_rate", thresholds.maximum_tool_error_rate),
        ("fallback_rate", thresholds.maximum_fallback_rate),
        ("tts_error_rate", thresholds.maximum_tts_error_rate),
    ):
        if rates[metric] > maximum:
            failures.append(f"{metric}_above_threshold")
    if _non_negative_float(final.get("avg_duration_ms")) > thresholds.maximum_average_duration_ms:
        failures.append("average_duration_above_threshold")
    if config_error := exporter_final.get("spool_error"):
        failures.append(f"observer_spool_error:{_bounded_text(config_error, maximum=80)}")
    if manitos_samples:
        if not exporter_final.get("enabled"):
            failures.append("manitos_observer_exporter_disabled")
        if exporter_final.get("privacy_mode") != "metadata_only":
            failures.append("privacy_mode_not_metadata_only")
        if thresholds.require_durable_delivery and not exporter_final.get("durable_delivery"):
            failures.append("durable_delivery_disabled")
        if exporter_deltas["dropped"]:
            failures.append("observer_envelopes_dropped")
        if exporter_deltas["spool_evicted"]:
            failures.append("observer_spool_evicted")
        if circuit_open_samples:
            failures.append("observer_circuit_open_during_window")
        if (
            _non_negative_int(exporter_final.get("persisted_pending"))
            > thresholds.maximum_persisted_pending
        ):
            failures.append("observer_persisted_pending_above_threshold")
    else:
        failures.append("manitos_readiness_unavailable")

    return {
        "passed": not failures,
        "failures": failures,
        "sample_count": len(samples),
        "observer_availability_rate": availability_rate,
        "manitos_availability_rate": manitos_availability_rate,
        "observed_turns": observed_turns,
        "quality_window_counts": window_counts,
        "quality_rates": rates,
        "final_average_duration_ms": _non_negative_float(final.get("avg_duration_ms")),
        "final_average_ttft_ms": _non_negative_float(final.get("avg_ttft_ms")),
        "exporter_stat_deltas": exporter_deltas,
        "final_persisted_pending": _non_negative_int(exporter_final.get("persisted_pending")),
        "circuit_open_samples": circuit_open_samples,
    }


def _safe_config(config: PassiveGateConfig) -> dict[str, Any]:
    result = asdict(config)
    result.pop("api_key", None)
    result["observer_url"] = _safe_url(config.observer_url)
    result["manitos_ready_url"] = _safe_url(config.manitos_ready_url)
    return result


def _write_report(path: str | Path, report: Mapping[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


async def run_passive_gate(config: PassiveGateConfig) -> dict[str, Any]:
    started_at = _utc_now()
    started = time.monotonic()
    samples: list[dict[str, Any]] = []
    timeout = httpx.Timeout(config.request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        while True:
            samples.append(await collect_sample(client, config, window_start=started_at))
            elapsed = time.monotonic() - started
            running = {
                "schema_version": "observer.manitos.passive_gate.v1",
                "status": "running",
                "started_at": started_at,
                "updated_at": _utc_now(),
                "config": _safe_config(config),
                "samples": samples,
            }
            _write_report(config.output_path, running)
            remaining = config.duration_seconds - elapsed
            if remaining <= 0:
                break
            await asyncio.sleep(min(config.interval_seconds, remaining))

    evaluation = evaluate_samples(samples, config.thresholds)
    report = {
        "schema_version": "observer.manitos.passive_gate.v1",
        "status": "passed" if evaluation["passed"] else "failed",
        "started_at": started_at,
        "finished_at": _utc_now(),
        "config": _safe_config(config),
        "evaluation": evaluation,
        "samples": samples,
    }
    _write_report(config.output_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Observe real ManitOS telemetry without generating conversations."
    )
    parser.add_argument("--observer-url", default=os.getenv("MANITOS_OBSERVER_URL", "http://127.0.0.1:8000"))
    parser.add_argument(
        "--manitos-ready-url",
        default=os.getenv("MANITOS_READY_URL", ""),
        help=(
            "Readiness probe URL for the integrated agent runtime "
            "(required for availability checks)"
        ),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("MANITOS_OBSERVER_API_KEY", ""),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--project-id", default=os.getenv("MANITOS_OBSERVER_PROJECT_ID", "manitos"))
    parser.add_argument("--environment", default=os.getenv("MANITOS_OBSERVER_ENVIRONMENT") or None)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--minimum-turns", type=int, default=20)
    parser.add_argument("--minimum-availability-rate", type=float, default=0.99)
    parser.add_argument("--maximum-error-rate", type=float, default=0.05)
    parser.add_argument("--maximum-degraded-rate", type=float, default=0.15)
    parser.add_argument("--maximum-truncated-rate", type=float, default=0.05)
    parser.add_argument("--maximum-tool-error-rate", type=float, default=0.10)
    parser.add_argument("--maximum-fallback-rate", type=float, default=0.25)
    parser.add_argument("--maximum-tts-error-rate", type=float, default=0.10)
    parser.add_argument("--maximum-average-duration-ms", type=float, default=60_000.0)
    parser.add_argument("--maximum-persisted-pending", type=int, default=0)
    parser.add_argument("--allow-volatile-delivery", action="store_true")
    parser.add_argument("--output", default=".observer-state/manitos-phase8.json")
    return parser


def config_from_args(args: argparse.Namespace) -> PassiveGateConfig:
    duration_seconds = (
        args.duration_seconds if args.duration_seconds is not None else args.duration_hours * 3600.0
    )
    thresholds = PassiveGateThresholds(
        minimum_turns=max(0, args.minimum_turns),
        minimum_availability_rate=args.minimum_availability_rate,
        maximum_error_rate=args.maximum_error_rate,
        maximum_degraded_rate=args.maximum_degraded_rate,
        maximum_truncated_rate=args.maximum_truncated_rate,
        maximum_tool_error_rate=args.maximum_tool_error_rate,
        maximum_fallback_rate=args.maximum_fallback_rate,
        maximum_tts_error_rate=args.maximum_tts_error_rate,
        maximum_average_duration_ms=args.maximum_average_duration_ms,
        maximum_persisted_pending=max(0, args.maximum_persisted_pending),
        require_durable_delivery=not args.allow_volatile_delivery,
    )
    return PassiveGateConfig(
        observer_url=args.observer_url,
        manitos_ready_url=args.manitos_ready_url,
        api_key=args.api_key,
        project_id=args.project_id,
        environment=args.environment,
        duration_seconds=duration_seconds,
        interval_seconds=args.interval_seconds,
        output_path=args.output,
        thresholds=thresholds,
    )


def main() -> int:
    config = config_from_args(build_parser().parse_args())
    try:
        report = asyncio.run(run_passive_gate(config))
    except KeyboardInterrupt:
        return 130
    evaluation = report["evaluation"]
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(Path(config.output_path).resolve()),
                "observed_turns": evaluation["observed_turns"],
                "failures": evaluation["failures"],
            },
            sort_keys=True,
        )
    )
    return 0 if evaluation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
