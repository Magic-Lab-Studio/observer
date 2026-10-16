"""Read-only MCP server: let an assistant (Claude, Codex) investigate Observer without screenshots.

``observer-mcp`` speaks MCP over stdio and talks to a running Observer through its HTTP API. It can
only read: every call is a ``GET`` or the metrics query (a ``POST`` that computes, never writes),
and the paths are fixed here. Trace and span **input/output are never returned** unless
``OBSERVER_MCP_CONTENT=1`` is set; what an assistant gets is names, types, status, durations,
attributes, scores and the provider chain: enough to diagnose a failure.

Configure with ``OBSERVER_URL`` (default http://127.0.0.1:8100) and ``OBSERVATORY_API_KEY``.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

DEFAULT_URL = "http://127.0.0.1:8100"
MAX_SPANS = 200
MAX_ROWS = 100
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


class ObserverReadError(RuntimeError):
    """Observer answered with an error or could not be reached (the message is safe to show)."""


def content_enabled() -> bool:
    return os.getenv("OBSERVER_MCP_CONTENT", "").strip().lower() in {"1", "true", "yes", "on"}


class ObserverReader:
    """HTTP access limited to reading. There is no method that writes."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        api_key: str = "",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            trust_env=False,
            transport=transport,
        )

    async def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self._send("GET", path, params=clean or None)

    async def query(self, body: Mapping[str, Any]) -> Any:
        """``POST /v1/analytics/query``: computes aggregates, changes nothing."""
        return await self._send("POST", "/v1/analytics/query", json=dict(body))

    async def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ObserverReadError(f"Observer unreachable ({exc.__class__.__name__})") from exc
        if response.status_code >= 400:
            raise ObserverReadError(f"{method} {path} answered {response.status_code}")
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


# ── shaping: compact, content-free views ──────────────────────────────────────


def _ms(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except ValueError:
        return None
    return round(delta.total_seconds() * 1000.0, 1)


def trace_row(trace: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": trace.get("id"),
        "name": trace.get("name"),
        "status": trace.get("status"),
        "project_id": trace.get("project_id"),
        "environment": trace.get("environment"),
        "created_at": trace.get("created_at"),
        "duration_ms": _ms(trace.get("start_time"), trace.get("end_time")),
    }


def span_view(span: Mapping[str, Any], *, content: bool) -> dict[str, Any]:
    out = {
        "id": span.get("id"),
        "parent_id": span.get("parent_id"),
        "name": span.get("name"),
        "type": span.get("span_type"),
        "status": span.get("status"),
        "duration_ms": _ms(span.get("start_time"), span.get("end_time")),
        "attributes": span.get("attributes") or {},
        "tokens_input": span.get("tokens_input"),
        "tokens_output": span.get("tokens_output"),
        "cost_usd": span.get("cost_usd"),
    }
    if content:
        out["input"], out["output"] = span.get("input"), span.get("output")
    return out


def evaluation_view(ev: Mapping[str, Any]) -> dict[str, Any]:
    result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
    return {
        "type": ev.get("evaluator_type"),
        "score": ev.get("score"),
        "grade": result.get("grade"),
        "passed": result.get("passed"),
        "health": result.get("health"),
        "correctness": result.get("correctness"),
        "criteria": [
            {k: c.get(k) for k in ("name", "score", "detail")}
            for c in result.get("criteria", [])
            if isinstance(c, dict)
        ],
    }


def trace_detail(
    trace: Mapping[str, Any],
    spans: list[Mapping[str, Any]],
    evaluations: list[Mapping[str, Any]],
    *,
    content: bool,
) -> dict[str, Any]:
    views = [span_view(s, content=content) for s in spans[:MAX_SPANS]]
    chain = next((s for s in views if s["name"] == "llm.chain"), None)
    tools = [
        {"name": s["name"], "status": s["status"], "duration_ms": s["duration_ms"]}
        for s in views
        if s["type"] == "tool"
    ]
    detail = trace_row(trace)
    detail.update(
        {
            "session_id": trace.get("session_id"),
            "metadata": trace.get("metadata") or {},
            "provider_chain": (chain or {}).get("attributes", {}).get("attempts_text"),
            "answered_by": (chain or {}).get("attributes", {}).get("answered_by"),
            "tools": tools,
            "evaluations": [evaluation_view(e) for e in evaluations],
            "spans": views,
            "spans_truncated": len(spans) > MAX_SPANS,
            "content_included": content,
        }
    )
    return detail


# ── the server ────────────────────────────────────────────────────────────────


def build_server(reader: ObserverReader) -> FastMCP:
    mcp = FastMCP(
        "observer",
        instructions=(
            "Read-only access to an Observer instance: traces of an AI agent, health and quality "
            "scores, provider fallbacks, alerts, test-case datasets and version comparisons. Start "
            "with observer_overview, then list_failed_traces, then get_trace for one failure."
        ),
    )

    def tool(fn):
        return mcp.tool(annotations=_READ_ONLY)(fn)

    @tool
    async def observer_overview(hours: int = 24, project_id: str = "manitos") -> dict[str, Any]:
        """Health at a glance: turn quality over the last `hours` and the open alerts."""
        quality = await reader.get(
            "/v1/analytics/manitos-quality", hours=hours, project_id=project_id
        )
        alerts = await reader.get("/v1/alerts/events", state="open", limit=20)
        return {"quality": quality, "open_alerts": alerts}

    @tool
    async def list_traces(
        status: str | None = None,
        environment: str | None = None,
        project_id: str | None = None,
        search: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Recent traces, newest first (status: ok | error). Metadata only."""
        data = await reader.get(
            "/v1/traces/",
            status=status,
            environment=environment,
            project_id=project_id,
            search=search,
            limit=max(1, min(limit, MAX_ROWS)),
        )
        return {"traces": [trace_row(t) for t in data.get("traces", [])]}

    @tool
    async def list_failed_traces(
        limit: int = 20, health_below: float = 0.6, project_id: str = "manitos"
    ) -> dict[str, Any]:
        """Traces that failed: status=error, plus turns whose operational-health score fell below
        `health_below` (rule-based evaluation). Each row says why."""
        limit = max(1, min(limit, MAX_ROWS))
        errors = await reader.get("/v1/traces/", status="error", project_id=project_id, limit=limit)
        low = await reader.get(
            "/v1/evaluations/",
            evaluator_type="rule_based",
            max_score=health_below,
            limit=limit,
        )
        rows: dict[str, dict[str, Any]] = {}
        for trace in errors.get("traces", []):
            rows[trace["id"]] = {**trace_row(trace), "reasons": ["trace status is error"]}
        for ev in low.get("evaluations", []):
            row = rows.setdefault(ev["trace_id"], {"id": ev["trace_id"], "reasons": []})
            view = evaluation_view(ev)
            row["reasons"].append(f"health score {view['score']} (grade {view['grade']})")
            row["health_score"] = view["score"]
        return {"count": len(rows), "traces": list(rows.values())[:limit]}

    @tool
    async def get_trace(trace_id: str) -> dict[str, Any]:
        """One trace in full detail without its text: spans (name, type, status, duration,
        attributes), tools called, the provider chain, and its scores. Input/output are omitted
        unless the server runs with OBSERVER_MCP_CONTENT=1."""
        trace = await reader.get(f"/v1/traces/{trace_id}")
        spans = await reader.get(f"/v1/traces/{trace_id}/spans")
        evaluations = await reader.get(f"/v1/traces/{trace_id}/evaluations")
        return trace_detail(trace, spans, evaluations, content=content_enabled())

    @tool
    async def list_alerts(state: str | None = None) -> dict[str, Any]:
        """Alert events (state: open | resolved) and how many are open."""
        return await reader.get("/v1/alerts/events", state=state, limit=50)

    @tool
    async def list_datasets() -> dict[str, Any]:
        """Test-case datasets with their case counts."""
        return {"datasets": await reader.get("/v1/datasets")}

    @tool
    async def get_dataset(dataset_id: str) -> dict[str, Any]:
        """A dataset with its cases: what was observed and what each case expects."""
        return await reader.get(f"/v1/datasets/{dataset_id}")

    @tool
    async def list_runs(dataset_id: str) -> dict[str, Any]:
        """Runs of a dataset (one per agent version), newest first, with pass/fail/unknown."""
        return {"runs": await reader.get(f"/v1/datasets/{dataset_id}/runs")}

    @tool
    async def compare_runs(
        dataset_id: str, base_run_id: str, candidate_run_id: str
    ) -> dict[str, Any]:
        """Case-by-case difference between two runs: regressed, improved, same, unknown, missing,
        with health and duration deltas."""
        return await reader.get(
            f"/v1/datasets/{dataset_id}/compare", base=base_run_id, candidate=candidate_run_id
        )

    @tool
    async def metrics_schema() -> dict[str, Any]:
        """Views, measures, dimensions and aggregations that query_metrics accepts."""
        return await reader.get("/v1/analytics/query/schema")

    @tool
    async def query_metrics(
        view: str,
        metrics: list[dict[str, str]],
        dimensions: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        hours: int = 24,
        granularity: str | None = None,
        row_limit: int = 50,
    ) -> dict[str, Any]:
        """Aggregate metrics (see metrics_schema), e.g. view="turns", metrics=[{"measure":
        "duration_ms", "aggregation": "p95"}], dimensions=["model"]. Read-only."""
        body: dict[str, Any] = {
            "view": view,
            "metrics": metrics,
            "dimensions": dimensions or [],
            "filters": filters or [],
            "hours": hours,
            "row_limit": max(1, min(row_limit, MAX_ROWS)),
        }
        if granularity:
            body["granularity"] = granularity
        return await reader.query(body)

    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="observer-mcp", description="Read-only MCP server for an Observer instance (stdio)."
    )
    parser.add_argument("--url", default=os.getenv("OBSERVER_URL", DEFAULT_URL))
    parser.add_argument("--api-key", default=os.getenv("OBSERVATORY_API_KEY", ""))
    args = parser.parse_args(argv)
    build_server(ObserverReader(args.url, args.api_key)).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
