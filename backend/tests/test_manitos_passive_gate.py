from __future__ import annotations

import httpx
import pytest

from app.ops.manitos_passive_gate import (
    GENERIC_REPORT_PATH,
    GENERIC_REPORT_SCHEMA,
    LEGACY_REPORT_PATH,
    LEGACY_REPORT_SCHEMA,
    IntegrationGateConfig,
    IntegrationGateThresholds,
    PassiveGateConfig,
    PassiveGateThresholds,
    build_parser,
    collect_sample,
    config_from_args,
    evaluate_samples,
    run_passive_gate,
)


def test_parser_uses_generic_configurable_health_and_report_defaults(monkeypatch):
    monkeypatch.delenv("OBSERVER_INTEGRATION_HEALTH_URL", raising=False)
    monkeypatch.delenv("MANITOS_READY_URL", raising=False)
    monkeypatch.delenv("OBSERVER_INTEGRATION_GATE_OUTPUT", raising=False)

    defaults = build_parser().parse_args([])
    assert defaults.manitos_ready_url == ""
    assert defaults.output == LEGACY_REPORT_PATH

    generic = build_parser().parse_args(
        ["--integration-health-url", "https://integration.example.test/health"]
    )
    legacy = build_parser().parse_args(
        ["--manitos-ready-url", "https://integration.example.test/health"]
    )
    assert generic.manitos_ready_url == legacy.manitos_ready_url

    monkeypatch.setenv("MANITOS_READY_URL", "https://legacy.example.test/health")
    monkeypatch.setenv(
        "OBSERVER_INTEGRATION_HEALTH_URL",
        "https://generic.example.test/health",
    )
    environment = build_parser().parse_args([])
    assert environment.manitos_ready_url == "https://generic.example.test/health"

    monkeypatch.delenv("OBSERVER_INTEGRATION_HEALTH_URL")
    legacy_environment = build_parser().parse_args([])
    assert legacy_environment.manitos_ready_url == "https://legacy.example.test/health"

    explicit = build_parser().parse_args(["--output", ".observer-state/explicit-report.json"])
    assert explicit.output == ".observer-state/explicit-report.json"

    generic_report = build_parser().parse_args(["--generic-report"])
    assert generic_report.generic_report == GENERIC_REPORT_PATH


def test_generic_names_and_legacy_cli_flags_remain_compatible():
    assert IntegrationGateConfig is PassiveGateConfig
    assert IntegrationGateThresholds is PassiveGateThresholds

    generic = build_parser().parse_args(
        [
            "--minimum-observations",
            "3",
            "--maximum-alternate-path-rate",
            "0.2",
            "--maximum-component-error-rate",
            "0.1",
        ]
    )
    legacy = build_parser().parse_args(
        [
            "--minimum-turns",
            "3",
            "--maximum-fallback-rate",
            "0.2",
            "--maximum-tts-error-rate",
            "0.1",
        ]
    )
    assert generic.minimum_turns == legacy.minimum_turns
    assert generic.maximum_fallback_rate == legacy.maximum_fallback_rate
    assert generic.maximum_tts_error_rate == legacy.maximum_tts_error_rate


def test_cli_help_prefers_generic_terminology():
    help_text = build_parser().format_help()

    for generic_flag in (
        "--integration-health-url",
        "--minimum-observations",
        "--maximum-alternate-path-rate",
        "--maximum-component-error-rate",
        "--maximum-pending-delivery-items",
        "--generic-report",
    ):
        assert generic_flag in help_text
    for legacy_flag in (
        "--manitos-ready-url",
        "--minimum-turns",
        "--maximum-fallback-rate",
        "--maximum-tts-error-rate",
        "--maximum-persisted-pending",
    ):
        assert legacy_flag not in help_text


def _sample(
    *,
    turns: int,
    errors: int = 0,
    tts_errors: int = 0,
    dropped: int = 0,
    circuit: str = "closed",
) -> dict:
    return {
        "observer": {"ok": True},
        "quality": {
            "total_turns": turns,
            "error_count": errors,
            "degraded_count": 0,
            "truncated_count": 0,
            "tool_error_count": 0,
            "fallback_count": 0,
            "tts_error_count": tts_errors,
            "avg_duration_ms": 1200,
            "avg_ttft_ms": 120,
        },
        "manitos": {
            "ok": True,
            "exporter": {
                "enabled": True,
                "privacy_mode": "metadata_only",
                "durable_delivery": True,
                "circuit_state": circuit,
                "persisted_pending": 0,
                "spool_error": None,
                "stats": {"dropped": dropped, "spool_evicted": 0},
            },
        },
    }


def test_evaluate_samples_passes_on_healthy_window():
    thresholds = PassiveGateThresholds(minimum_turns=2)

    result = evaluate_samples([_sample(turns=10), _sample(turns=12)], thresholds)

    assert result["passed"] is True
    assert result["observed_turns"] == 12
    assert result["quality_rates"]["error_rate"] == 0.0


def test_evaluate_samples_fails_on_quality_and_delivery_regressions():
    thresholds = PassiveGateThresholds(minimum_turns=2, maximum_error_rate=0.2)

    result = evaluate_samples(
        [_sample(turns=10), _sample(turns=10, errors=3, dropped=1, circuit="open")],
        thresholds,
    )

    assert result["passed"] is False
    assert "error_rate_above_threshold" in result["failures"]
    assert "observer_envelopes_dropped" in result["failures"]
    assert "observer_circuit_open_during_window" in result["failures"]


def test_evaluate_samples_error_rate_uses_window_counts_not_snapshot_deltas():
    thresholds = PassiveGateThresholds(minimum_turns=2, maximum_error_rate=0.2)

    result = evaluate_samples(
        [_sample(turns=10, errors=6), _sample(turns=12, errors=6)], thresholds
    )

    assert result["passed"] is False
    assert result["quality_window_counts"]["error_count"] == 6
    assert "error_rate_above_threshold" in result["failures"]


def test_evaluate_samples_fails_closed_without_real_turns():
    result = evaluate_samples([_sample(turns=10), _sample(turns=10)], PassiveGateThresholds())

    assert result["passed"] is False
    assert "insufficient_observed_turns" in result["failures"]


def test_evaluate_samples_fails_when_manitos_unavailable_at_end():
    thresholds = PassiveGateThresholds(minimum_turns=2)
    samples = [_sample(turns=10), {**_sample(turns=10), "manitos": {"ok": False}}]

    result = evaluate_samples(samples, thresholds)

    assert result["passed"] is False
    assert result["manitos_availability_rate"] == 0.5
    assert "manitos_availability_below_threshold" in result["failures"]
    assert "manitos_not_ready_at_end" in result["failures"]


def test_evaluate_samples_fails_when_manitos_mostly_down():
    thresholds = PassiveGateThresholds(minimum_turns=2)
    samples = [
        _sample(turns=10),
        {**_sample(turns=10), "manitos": {"ok": False}},
        {**_sample(turns=10), "manitos": {"ok": False}},
    ]

    result = evaluate_samples(samples, thresholds)

    assert result["passed"] is False
    assert result["manitos_availability_rate"] == 1 / 3
    assert "manitos_availability_below_threshold" in result["failures"]


@pytest.mark.asyncio
async def test_collect_sample_keeps_only_bounded_metadata():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy", "db": "ok"})
        if request.url.path == "/v1/analytics/manitos-quality":
            captured["query"] = str(request.url.query)
            return httpx.Response(
                200,
                json={
                    "project_id": "manitos",
                    "environment": "test",
                    "hours": 24,
                    "window_start": "2026-07-30T00:00:00+00:00",
                    "window_end": "2026-07-30T00:05:00+00:00",
                    "total_turns": 3,
                    "error_count": 0,
                    "models": [{"key": "phi4-mini", "count": 3, "secret": "drop-me"}],
                    "prompt": "must-not-survive",
                },
            )
        if request.url.path == "/readyz":
            return httpx.Response(
                200,
                json={
                    "ready": True,
                    "features": {
                        "observer_exporter": {
                            "enabled": True,
                            "privacy_mode": "metadata_only",
                            "durable_delivery": True,
                            "circuit_state": "closed",
                            "stats": {"accepted": 3},
                            "api_key": "must-not-survive",
                        }
                    },
                },
            )
        return httpx.Response(404)

    config = PassiveGateConfig(
        observer_url="http://observer",
        manitos_ready_url="http://manitos/readyz",
        duration_seconds=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sample = await collect_sample(client, config, window_start="2026-07-30T00:00:00+00:00")

    assert sample["observer"]["ok"] is True
    assert "since=2026-07-30T00%3A00%3A00%2B00%3A00" in captured["query"]
    assert sample["quality"]["window_start"] == "2026-07-30T00:00:00+00:00"
    assert sample["quality"]["window_end"] == "2026-07-30T00:05:00+00:00"
    assert sample["quality"]["models"] == [{"key": "phi4-mini", "count": 3}]
    assert "prompt" not in sample["quality"]
    assert "api_key" not in sample["manitos"]["exporter"]


@pytest.mark.asyncio
async def test_collect_sample_without_window_uses_rolling_hours():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/analytics/manitos-quality":
            captured["query"] = str(request.url.query)
            return httpx.Response(200, json={"total_turns": 0})
        return httpx.Response(404)

    config = PassiveGateConfig(observer_url="http://observer", duration_seconds=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await collect_sample(client, config)

    assert "since" not in captured["query"]
    assert "hours=720" in captured["query"]


@pytest.mark.asyncio
async def test_collect_sample_without_health_url_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy", "db": "ok"})
        if request.url.path == "/v1/analytics/manitos-quality":
            return httpx.Response(200, json={"total_turns": 0})
        return httpx.Response(404)

    config = PassiveGateConfig(observer_url="http://observer", duration_seconds=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sample = await collect_sample(client, config)

    assert sample["manitos"]["ok"] is False
    assert sample["manitos"]["status_code"] == 0
    assert sample["manitos"]["error"] == "not_configured"
    result = evaluate_samples(
        [sample],
        PassiveGateThresholds(minimum_turns=0, require_durable_delivery=False),
    )
    assert result["manitos_availability_rate"] == 0.0
    assert "manitos_readiness_unavailable" in result["failures"]


@pytest.mark.asyncio
async def test_report_never_persists_api_key_or_url_credentials(tmp_path, monkeypatch):
    async def fake_collect(_client, _config, **kwargs):
        return _sample(turns=0)

    monkeypatch.setattr("app.ops.manitos_passive_gate.collect_sample", fake_collect)
    output = tmp_path / "gate.json"
    config = PassiveGateConfig(
        observer_url="http://user:password@observer.local:8000?token=url-secret",
        manitos_ready_url="http://manitos.local:8765/readyz?token=ready-secret",
        api_key="header-secret",
        duration_seconds=0,
        output_path=str(output),
        thresholds=PassiveGateThresholds(minimum_turns=0),
    )

    report = await run_passive_gate(config)

    persisted = output.read_text(encoding="utf-8")
    assert report["schema_version"] == LEGACY_REPORT_SCHEMA
    assert "report_schema" not in report["config"]
    assert "password" not in persisted
    assert "url-secret" not in persisted
    assert "ready-secret" not in persisted
    assert "header-secret" not in persisted


@pytest.mark.asyncio
async def test_generic_v2_is_additive_and_omits_legacy_delivery_details(tmp_path, monkeypatch):
    async def fake_collect(_client, _config, **kwargs):
        return _sample(turns=10, tts_errors=2, dropped=1, circuit="open")

    monkeypatch.setattr("app.ops.manitos_passive_gate.collect_sample", fake_collect)
    output = tmp_path / "integration-gate-report.json"
    config = PassiveGateConfig(
        observer_url="http://observer",
        manitos_ready_url="http://integration/health",
        duration_seconds=0,
        output_path=str(output),
        report_schema=GENERIC_REPORT_SCHEMA,
        thresholds=PassiveGateThresholds(minimum_turns=1),
    )

    report = await run_passive_gate(config)
    persisted = output.read_text(encoding="utf-8")

    assert report["schema_version"] == GENERIC_REPORT_SCHEMA
    assert report["evaluation"]["observed_operations"] == 10
    assert report["evaluation"]["quality_rates"]["component_error_rate"] == 0.2
    assert report["samples"][0]["integration"]["ok"] is True
    assert "exporter" not in report["samples"][0]["integration"]
    assert "observer_url" not in report["config"]
    assert "integration_health_url" not in report["config"]
    assert "output_path" not in report["config"]
    for legacy_key in (
        "manitos",
        "tts_error_count",
        "fallback_count",
        "spool_error",
        "circuit_state",
        "durable_delivery",
        "persisted_pending",
    ):
        assert f'"{legacy_key}":' not in persisted


def test_generic_report_mode_selects_v2_without_changing_legacy_default():
    legacy = config_from_args(build_parser().parse_args([]))
    generic = config_from_args(build_parser().parse_args(["--generic-report"]))

    assert legacy.report_schema == LEGACY_REPORT_SCHEMA
    assert legacy.output_path == LEGACY_REPORT_PATH
    assert generic.report_schema == GENERIC_REPORT_SCHEMA
    assert generic.output_path == GENERIC_REPORT_PATH
