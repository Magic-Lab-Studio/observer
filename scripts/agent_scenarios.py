#!/usr/bin/env python3
"""Six synthetic agent scenarios sent through the Python SDK to a running Observer, then read back
and checked through the public API.

The scenarios follow the external evaluation of 2026-10-18 (success, tool failure, slow tool,
truncated model response, HTTP 429 then a successful retry, fifteen repeated tool calls). This is
an independent reimplementation, not the evaluator's own script. The model calls go through the
real ``anthropic`` client with a mocked HTTP transport: no API key, no network, no cost. It is not
a live provider test.

    python scripts/agent_scenarios.py --endpoint http://127.0.0.1:8000 [--slow-seconds 25]

Exit status 0 when every check holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

try:  # current provider SDKs ship their own fork of httpx
    import httpx2 as httpx
except ImportError:
    import httpx

import anthropic
import llm_observatory as obs
from llm_observatory.instrumentors.anthropic import AnthropicInstrumentor

MODEL = "claude-sonnet-4-5-20250929"


def _message(text: str, stop: str = "end_turn", out_tokens: int = 12) -> dict:
    return {
        "id": "msg_scenario",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 40, "output_tokens": out_tokens},
    }


def _client(responses: list[tuple[int, dict]]) -> anthropic.Anthropic:
    queue = list(responses)

    def handler(request):
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=body)

    return anthropic.Anthropic(
        api_key="scenario", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _ask(client: anthropic.Anthropic, prompt: str):
    return client.messages.create(
        model=MODEL, max_tokens=64, messages=[{"role": "user", "content": prompt}]
    )


def _tool(name: str, fn, *args):
    @obs.trace(name=name, span_type="tool")
    def run():
        return fn(*args)

    return run()


def scenario_success(slow: float) -> None:
    client = _client([(200, _message("Bogotá"))])

    @obs.trace(name="scenario.success", span_type="agent")
    def agent():
        _tool("weather", lambda city: {"c": 18}, "Bogotá")
        return _ask(client, "weather?")

    agent()


def scenario_tool_failure(slow: float) -> None:
    @obs.trace(name="scenario.tool_failure", span_type="agent")
    def agent():
        def broken(_):
            raise ConnectionError("search backend unreachable")

        _tool("search", broken, "q")

    try:
        agent()
    except ConnectionError:
        pass


def scenario_slow_tool(slow: float) -> None:
    @obs.trace(name="scenario.slow_tool", span_type="agent")
    def agent():
        _tool("slow_report", time.sleep, slow)

    agent()


def scenario_truncated(slow: float) -> None:
    client = _client([(200, _message("The answer is", stop="max_tokens", out_tokens=64))])

    @obs.trace(name="scenario.truncated", span_type="agent")
    def agent():
        return _ask(client, "long answer please")

    agent()


def scenario_retry_429(slow: float) -> None:
    rate_limited = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    client = _client([(429, rate_limited), (200, _message("ok after retry"))])

    @obs.trace(name="scenario.retry_429", span_type="agent")
    def agent():
        for attempt in range(3):
            try:
                return _ask(client, "hi")
            except anthropic.RateLimitError:
                time.sleep(0.2 * (attempt + 1))
        raise RuntimeError("gave up")

    agent()


def scenario_repeated_tool(slow: float) -> None:
    @obs.trace(name="scenario.repeated_tool", span_type="agent")
    def agent():
        for _ in range(15):
            _tool("lookup_order", lambda order: None, "A-17")

    agent()


SCENARIOS = {
    "scenario.success": scenario_success,
    "scenario.tool_failure": scenario_tool_failure,
    "scenario.slow_tool": scenario_slow_tool,
    "scenario.truncated": scenario_truncated,
    "scenario.retry_429": scenario_retry_429,
    "scenario.repeated_tool": scenario_repeated_tool,
}


def _get(endpoint: str, path: str, api_key: str | None):
    request = urllib.request.Request(endpoint + path)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _check(endpoint: str, api_key: str | None, slow: float) -> list[tuple[str, bool, str]]:
    listed = _get(endpoint, "/v1/traces/?search=scenario.&limit=100", api_key)["traces"]
    by_name = {t["name"]: t for t in listed}
    results: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str) -> None:
        results.append((label, bool(ok), detail))

    for name in SCENARIOS:
        check(f"{name}: stored under its root name", name in by_name, "")
    if len(by_name) < len(SCENARIOS):
        return results

    def spans(name):
        return _get(endpoint, f"/v1/traces/{by_name[name]['id']}/spans", api_key)

    def duration(trace) -> float | None:
        if not trace["end_time"]:
            return None
        from datetime import datetime

        return (
            datetime.fromisoformat(trace["end_time"]) - datetime.fromisoformat(trace["start_time"])
        ).total_seconds()

    ok_trace = by_name["scenario.success"]
    llm = [s for s in spans("scenario.success") if s["span_type"] == "llm"]
    check("success: trace ok", ok_trace["status"] == "ok", ok_trace["status"])
    check("success: llm span finished with tokens", bool(llm) and llm[0]["end_time"] is not None
          and llm[0]["tokens_input"] == 40, json.dumps(llm[:1])[:160])
    check("success: llm span priced (dated Claude id)", bool(llm) and llm[0]["cost_usd"] is not None,
          str(llm[0]["cost_usd"] if llm else None))

    failed = by_name["scenario.tool_failure"]
    tool = [s for s in spans("scenario.tool_failure") if s["name"] == "search"]
    check("tool failure: trace error", failed["status"] == "error", failed["status"])
    check("tool failure: tool span error with type", bool(tool) and tool[0]["status"] == "error"
          and tool[0]["attributes"].get("error.type") == "ConnectionError", json.dumps(tool[:1])[:160])

    slow_trace = by_name["scenario.slow_tool"]
    measured = duration(slow_trace)
    check(f"slow tool: duration about {slow:g} s", measured is not None and abs(measured - slow) < 1.0,
          f"{measured}")

    truncated = [s for s in spans("scenario.truncated") if s["span_type"] == "llm"]
    reasons = truncated[0]["attributes"].get("gen_ai.response.finish_reasons") if truncated else None
    check("truncated: finish reason max_tokens recorded", reasons == ["max_tokens"], str(reasons))

    retry = by_name["scenario.retry_429"]
    attempts = sorted((s for s in spans("scenario.retry_429") if s["span_type"] == "llm"),
                      key=lambda s: s["start_time"])
    check("429 retry: operation ok", retry["status"] == "ok", retry["status"])
    check("429 retry: failed attempt kept as an error span",
          [a["status"] for a in attempts] == ["error", "ok"], str([a["status"] for a in attempts]))

    repeated = [s for s in spans("scenario.repeated_tool") if s["name"] == "lookup_order"]
    check("repeated tool: 15 calls stored", len(repeated) == 15, str(len(repeated)))

    summary = _get(endpoint, "/v1/analytics/summary?hours=1", api_key)
    check("summary: traces counted", summary["total_traces"] >= len(SCENARIOS), str(summary["total_traces"]))
    check("summary: the failed scenario counts as an error", summary["error_count"] >= 1,
          str(summary["error_count"]))
    check("summary: running traces none", summary.get("running_traces") == 0,
          str(summary.get("running_traces")))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--slow-seconds", type=float, default=25.0)
    parser.add_argument("--only", choices=sorted(SCENARIOS), action="append")
    args = parser.parse_args()

    tracer = obs.Tracer(service_name="observer-scenarios", batch_size=50, flush_interval=1.0)
    tracer.add_exporter(obs.OTLPExporter(endpoint=args.endpoint, api_key=args.api_key))
    obs.set_tracer(tracer)
    instrumentor = AnthropicInstrumentor()
    instrumentor.instrument()
    try:
        for name, run in SCENARIOS.items():
            if args.only and name not in args.only:
                continue
            started = time.time()
            run(args.slow_seconds)
            print(f"ran {name} ({time.time() - started:.1f} s)")
    finally:
        instrumentor.uninstrument()
        tracer.shutdown()

    if args.only:
        return 0
    failures = 0
    for label, ok, detail in _check(args.endpoint, args.api_key, args.slow_seconds):
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not ok else ""))
    print(f"{failures} failed" if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
