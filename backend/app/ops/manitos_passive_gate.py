"""Passive, metadata-only compatibility gate for an Observer integration."""

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

LEGACY_REPORT_SCHEMA = "observer.manitos.passive_gate.v1"
GENERIC_REPORT_SCHEMA = "observer.integration.passive_gate.v2"
LEGACY_REPORT_PATH = ".observer-state/manitos-phase8.json"
GENERIC_REPORT_PATH = ".observer-state/integration-gate-report.json"

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
    """Compatibility evaluation parameters, not a production policy."""

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
    manitos_ready_url: str = ""
    api_key: str = field(default="", repr=False)
    project_id: str = "manitos"
    environment: str | None = None
    analytics_hours: int = 720
    duration_seconds: float = 86_400.0
    interval_seconds: float = 60.0
    request_timeout_seconds: float = 5.0
    output_path: str = LEGACY_REPORT_PATH
    thresholds: PassiveGateThresholds = field(default_factory=PassiveGateThresholds)
    report_schema: str = LEGACY_REPORT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "observer_url", self.observer_url.rstrip("/"))
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        if self.interval_seconds < 0.1:
            raise ValueError("interval_seconds must be at least 0.1")
        if not 1 <= self.analytics_hours <= 720:
            raise ValueError("analytics_hours must be between 1 and 720")
        if self.report_schema not in {LEGACY_REPORT_SCHEMA, GENERIC_REPORT_SCHEMA}:
            raise ValueError("unsupported report_schema")


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
    ready_ok = False
    ready_code = 0
    ready_error: str | None = "not_configured"
    ready: dict[str, Any] = {}
    if config.manitos_ready_url:
        ready_ok, ready_code, ready, ready_error = await _get_json(client, config.manitos_ready_url)
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


def _generic_quality(payload: Mapping[str, Any]) -> dict[str, Any]:
    aliases = {
        "error_count": "error_count",
        "degraded_count": "impaired_count",
        "truncated_count": "incomplete_output_count",
        "tool_error_count": "operation_error_count",
        "fallback_count": "alternate_path_count",
        "tts_error_count": "component_error_count",
    }
    result = {key: value for key, value in payload.items() if key not in _QUALITY_COUNT_KEYS}
    result.update(
        {generic: _non_negative_int(payload.get(legacy)) for legacy, generic in aliases.items()}
    )
    return result


def _generic_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    integration = sample.get("manitos") if isinstance(sample.get("manitos"), Mapping) else {}
    return {
        "sampled_at": sample.get("sampled_at"),
        "observer": dict(sample.get("observer") or {}),
        "integration": {
            "ok": integration.get("ok") is True,
            "status_code": _non_negative_int(integration.get("status_code")),
            "error": _bounded_text(integration.get("error"), maximum=80) or None,
        },
        "quality": _generic_quality(sample.get("quality") or {}),
    }


def _generic_failure(value: str) -> str:
    name = str(value).split(":", 1)[0]
    aliases = {
        "manitos_availability_below_threshold": "integration_availability_below_threshold",
        "manitos_not_ready_at_end": "integration_unhealthy_at_end",
        "degraded_rate_above_threshold": "impaired_rate_above_threshold",
        "truncated_rate_above_threshold": "incomplete_output_rate_above_threshold",
        "tool_error_rate_above_threshold": "operation_error_rate_above_threshold",
        "fallback_rate_above_threshold": "alternate_path_rate_above_threshold",
        "tts_error_rate_above_threshold": "component_error_rate_above_threshold",
        "observer_spool_error": "integration_delivery_error",
        "manitos_observer_exporter_disabled": "integration_export_disabled",
        "durable_delivery_disabled": "persistence_requirement_not_met",
        "observer_envelopes_dropped": "delivery_items_dropped",
        "observer_spool_evicted": "persisted_items_evicted",
        "observer_circuit_open_during_window": "delivery_temporarily_unavailable",
        "observer_persisted_pending_above_threshold": "pending_delivery_above_threshold",
        "manitos_readiness_unavailable": "integration_health_unavailable",
    }
    return aliases.get(name, name)


def _generic_evaluation(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    count_aliases = {
        "error_count": "error_count",
        "degraded_count": "impaired_count",
        "truncated_count": "incomplete_output_count",
        "tool_error_count": "operation_error_count",
        "fallback_count": "alternate_path_count",
        "tts_error_count": "component_error_count",
    }
    rate_aliases = {
        "error_rate": "error_rate",
        "degraded_rate": "impaired_rate",
        "truncated_rate": "incomplete_output_rate",
        "tool_error_rate": "operation_error_rate",
        "fallback_rate": "alternate_path_rate",
        "tts_error_rate": "component_error_rate",
    }
    counts = evaluation.get("quality_window_counts") or {}
    rates = evaluation.get("quality_rates") or {}
    return {
        "passed": evaluation.get("passed") is True,
        "failures": [_generic_failure(value) for value in evaluation.get("failures") or []],
        "sample_count": _non_negative_int(evaluation.get("sample_count")),
        "observer_availability_rate": _non_negative_float(
            evaluation.get("observer_availability_rate")
        ),
        "integration_availability_rate": _non_negative_float(
            evaluation.get("manitos_availability_rate")
        ),
        "observed_operations": _non_negative_int(evaluation.get("observed_turns")),
        "quality_window_counts": {
            generic: _non_negative_int(counts.get(legacy))
            for legacy, generic in count_aliases.items()
        },
        "quality_rates": {
            generic: _non_negative_float(rates.get(legacy))
            for legacy, generic in rate_aliases.items()
        },
        "final_average_duration_ms": _non_negative_float(
            evaluation.get("final_average_duration_ms")
        ),
        "final_average_initial_response_ms": _non_negative_float(
            evaluation.get("final_average_ttft_ms")
        ),
    }


def _safe_config(config: PassiveGateConfig) -> dict[str, Any]:
    result = asdict(config)
    result.pop("api_key", None)
    result.pop("report_schema", None)
    result["observer_url"] = _safe_url(config.observer_url)
    result["manitos_ready_url"] = _safe_url(config.manitos_ready_url)
    if config.report_schema == GENERIC_REPORT_SCHEMA:
        thresholds = result.pop("thresholds")
        result.pop("observer_url", None)
        result.pop("manitos_ready_url", None)
        result.pop("output_path", None)
        result["evaluation_parameters"] = {
            "minimum_observations": thresholds["minimum_turns"],
            "minimum_availability_rate": thresholds["minimum_availability_rate"],
            "maximum_error_rate": thresholds["maximum_error_rate"],
            "maximum_impaired_rate": thresholds["maximum_degraded_rate"],
            "maximum_incomplete_output_rate": thresholds["maximum_truncated_rate"],
            "maximum_operation_error_rate": thresholds["maximum_tool_error_rate"],
            "maximum_alternate_path_rate": thresholds["maximum_fallback_rate"],
            "maximum_component_error_rate": thresholds["maximum_tts_error_rate"],
            "maximum_average_duration_ms": thresholds["maximum_average_duration_ms"],
            "maximum_pending_delivery_items": thresholds["maximum_persisted_pending"],
            "require_persistent_delivery": thresholds["require_durable_delivery"],
        }
    return result


def _report_samples(samples: list[dict[str, Any]], schema: str) -> list[dict[str, Any]]:
    if schema == GENERIC_REPORT_SCHEMA:
        return [_generic_sample(sample) for sample in samples]
    return samples


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
                "schema_version": config.report_schema,
                "status": "running",
                "started_at": started_at,
                "updated_at": _utc_now(),
                "config": _safe_config(config),
                "samples": _report_samples(samples, config.report_schema),
            }
            _write_report(config.output_path, running)
            remaining = config.duration_seconds - elapsed
            if remaining <= 0:
                break
            await asyncio.sleep(min(config.interval_seconds, remaining))

    evaluation = evaluate_samples(samples, config.thresholds)
    public_evaluation = (
        _generic_evaluation(evaluation)
        if config.report_schema == GENERIC_REPORT_SCHEMA
        else evaluation
    )
    report = {
        "schema_version": config.report_schema,
        "status": "passed" if evaluation["passed"] else "failed",
        "started_at": started_at,
        "finished_at": _utc_now(),
        "config": _safe_config(config),
        "evaluation": public_evaluation,
        "samples": _report_samples(samples, config.report_schema),
    }
    _write_report(config.output_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a configurable, metadata-only integration telemetry window."
    )
    parser.add_argument(
        "--observer-url", default=os.getenv("MANITOS_OBSERVER_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument(
        "--integration-health-url",
        dest="manitos_ready_url",
        default=os.getenv(
            "OBSERVER_INTEGRATION_HEALTH_URL",
            os.getenv("MANITOS_READY_URL", ""),
        ),
        help="Optional integration health URL (legacy flag remains supported).",
    )
    parser.add_argument(
        "--manitos-ready-url",
        dest="manitos_ready_url",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
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
    parser.add_argument(
        "--minimum-observations",
        dest="minimum_turns",
        type=int,
        default=20,
        help="Minimum observations required by this caller's evaluation policy.",
    )
    parser.add_argument(
        "--minimum-turns",
        dest="minimum_turns",
        type=int,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--minimum-availability-rate", type=float, default=0.99)
    parser.add_argument("--maximum-error-rate", type=float, default=0.05)
    parser.add_argument(
        "--maximum-impaired-rate", dest="maximum_degraded_rate", type=float, default=0.15
    )
    parser.add_argument(
        "--maximum-degraded-rate",
        dest="maximum_degraded_rate",
        type=float,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--maximum-incomplete-output-rate", dest="maximum_truncated_rate", type=float, default=0.05
    )
    parser.add_argument(
        "--maximum-truncated-rate",
        dest="maximum_truncated_rate",
        type=float,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--maximum-operation-error-rate", dest="maximum_tool_error_rate", type=float, default=0.10
    )
    parser.add_argument(
        "--maximum-tool-error-rate",
        dest="maximum_tool_error_rate",
        type=float,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--maximum-alternate-path-rate", dest="maximum_fallback_rate", type=float, default=0.25
    )
    parser.add_argument(
        "--maximum-fallback-rate",
        dest="maximum_fallback_rate",
        type=float,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--maximum-component-error-rate", dest="maximum_tts_error_rate", type=float, default=0.10
    )
    parser.add_argument(
        "--maximum-tts-error-rate",
        dest="maximum_tts_error_rate",
        type=float,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--maximum-average-duration-ms", type=float, default=60_000.0)
    parser.add_argument(
        "--maximum-pending-delivery-items", dest="maximum_persisted_pending", type=int, default=0
    )
    parser.add_argument(
        "--maximum-persisted-pending",
        dest="maximum_persisted_pending",
        type=int,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-nonpersistent-delivery", dest="allow_volatile_delivery", action="store_true"
    )
    parser.add_argument(
        "--allow-volatile-delivery",
        dest="allow_volatile_delivery",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--output",
        default=os.getenv(
            "OBSERVER_INTEGRATION_GATE_OUTPUT",
            LEGACY_REPORT_PATH,
        ),
    )
    output_group.add_argument(
        "--generic-report",
        nargs="?",
        const=GENERIC_REPORT_PATH,
        metavar="PATH",
        help="Emit the additive generic v2 report, optionally at PATH.",
    )
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
    generic_output = getattr(args, "generic_report", None)
    return PassiveGateConfig(
        observer_url=args.observer_url,
        manitos_ready_url=args.manitos_ready_url,
        api_key=args.api_key,
        project_id=args.project_id,
        environment=args.environment,
        duration_seconds=duration_seconds,
        interval_seconds=args.interval_seconds,
        output_path=generic_output or args.output,
        thresholds=thresholds,
        report_schema=GENERIC_REPORT_SCHEMA if generic_output else LEGACY_REPORT_SCHEMA,
    )


def main() -> int:
    config = config_from_args(build_parser().parse_args())
    try:
        report = asyncio.run(run_passive_gate(config))
    except KeyboardInterrupt:
        return 130
    evaluation = report["evaluation"]
    observed_key = (
        "observed_operations" if config.report_schema == GENERIC_REPORT_SCHEMA else "observed_turns"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(Path(config.output_path).resolve()),
                observed_key: evaluation[observed_key],
                "failures": evaluation["failures"],
            },
            sort_keys=True,
        )
    )
    return 0 if evaluation["passed"] else 1


# Additive generic names for new callers; legacy imports remain supported.
IntegrationGateThresholds = PassiveGateThresholds
IntegrationGateConfig = PassiveGateConfig
run_integration_gate = run_passive_gate


if __name__ == "__main__":
    raise SystemExit(main())
