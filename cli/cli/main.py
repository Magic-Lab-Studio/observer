"""Main CLI application for LLM Observatory."""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="llm-observatory")
@click.option("--api-key", envvar="OBSERVATORY_API_KEY", default=None, help="API key for backend authentication")
@click.pass_context
def cli(ctx: click.Context, api_key: str | None) -> None:
    """LLM Observatory CLI - Open-source LLM observability."""
    ctx.ensure_object(dict)
    ctx.obj["api_key"] = api_key


# ── Local Database Commands ──────────────────────────────────────────


@cli.command()
@click.option("--db", type=click.Path(exists=True), default="observatory.db", help="SQLite database path")
@click.option("--limit", default=20, help="Number of traces to show")
@click.option("--status", type=click.Choice(["ok", "error", "unset"]), help="Filter by status")
@click.option("--search", help="Search trace names")
def traces(db: str, limit: int, status: str | None, search: str | None) -> None:
    """List traces from local SQLite database."""
    from cli.db import list_traces

    rows = list_traces(Path(db), limit=limit, status=status, name_search=search)

    if not rows:
        console.print("[yellow]No traces found[/yellow]")
        return

    table = Table(title=f"Traces ({len(rows)} shown)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Spans", justify="right")
    table.add_column("Created", style="blue")

    for row in rows:
        table.add_row(
            str(row["id"])[:8],
            str(row["name"]),
            str(row["status"]),
            "",
            str(row.get("created_at", "")),
        )

    console.print(table)


@cli.command()
@click.argument("trace_id")
@click.option("--db", type=click.Path(exists=True), default="observatory.db", help="SQLite database path")
def inspect(trace_id: str, db: str) -> None:
    """Inspect a trace and its spans."""
    from cli.db import get_trace, get_trace_spans

    trace_data = get_trace(trace_id, Path(db))
    if not trace_data:
        console.print(f"[red]Trace {trace_id} not found[/red]")
        return

    # Print trace info
    console.print(f"\n[bold cyan]Trace: {trace_data['name']}[/bold cyan]")
    console.print(f"  ID:       {trace_data['id']}")
    console.print(f"  Status:   {trace_data['status']}")
    console.print(f"  Created:  {trace_data.get('created_at', 'N/A')}")

    # Print spans
    spans = get_trace_spans(trace_id, Path(db))
    if spans:
        console.print(f"\n[bold]Spans ({len(spans)}):[/bold]")
        table = Table()
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Status")
        table.add_column("Tokens In", justify="right")
        table.add_column("Tokens Out", justify="right")
        table.add_column("Cost", justify="right")

        for span in spans:
            table.add_row(
                str(span["id"])[:8],
                str(span["name"]),
                str(span["span_type"]),
                str(span["status"]),
                str(span.get("tokens_input") or ""),
                str(span.get("tokens_output") or ""),
                f"${span['cost_usd']:.6f}" if span.get("cost_usd") else "",
            )

        console.print(table)
    else:
        console.print("[yellow]No spans found[/yellow]")


@cli.command(name="import")
@click.argument("file", type=click.Path(exists=True))
@click.option("--db", type=click.Path(), default="observatory.db", help="SQLite database path")
@click.option("--dry-run", is_flag=True, help="Preview without writing")
def import_data(file: str, db: str, dry_run: bool) -> None:
    """Import traces from a JSON export file."""
    import json

    from cli.db import get_connection

    raw = json.loads(Path(file).read_text())
    traces = raw.get("traces", [])

    if not traces:
        console.print("[yellow]No traces in file[/yellow]")
        return

    console.print(f"[blue]Importing {len(traces)} traces from {file}...[/blue]")

    if dry_run:
        for t in traces:
            n = len(t.get("spans", []))
            console.print(f"  {t.get('name', '?')} — {n} spans")
        console.print("[yellow]Dry run, no changes written[/yellow]")
        return

    conn = get_connection(Path(db))
    try:
        imported = 0
        for trace in traces:
            conn.execute(
                "INSERT OR IGNORE INTO traces (id, name, session_id, status, metadata, start_time, end_time, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    trace["id"],
                    trace["name"],
                    trace.get("session_id"),
                    trace.get("status", "unset"),
                    json.dumps(trace.get("metadata")),
                    trace.get("start_time"),
                    trace.get("end_time"),
                    trace.get("created_at"),
                ),
            )
            for span in trace.get("spans", []):
                conn.execute(
                    "INSERT OR IGNORE INTO spans (id, trace_id, parent_span_id, name, span_type, start_time, end_time, status, input, output, tokens_input, tokens_output, cost_usd, metadata, attributes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        span["id"],
                        span.get("trace_id", trace["id"]),
                        span.get("parent_span_id"),
                        span["name"],
                        span.get("span_type", "generic"),
                        span.get("start_time"),
                        span.get("end_time"),
                        span.get("status", "unset"),
                        json.dumps(span.get("input")) if span.get("input") else None,
                        json.dumps(span.get("output")) if span.get("output") else None,
                        span.get("tokens_input"),
                        span.get("tokens_output"),
                        span.get("cost_usd"),
                        json.dumps(span.get("metadata")) if span.get("metadata") else None,
                        json.dumps(span.get("attributes")) if span.get("attributes") else None,
                    ),
                )
            imported += 1
        conn.commit()
        console.print(f"[green]Imported {imported} traces ({sum(len(t.get('spans', [])) for t in traces)} spans)[/green]")
    finally:
        conn.close()


@cli.command()
@click.option("--db", type=click.Path(exists=True), default="observatory.db", help="SQLite database path")
def stats(db: str) -> None:
    """Show summary statistics."""
    from cli.db import get_stats

    data = get_stats(Path(db))

    console.print("\n[bold cyan]LLM Observatory Statistics[/bold cyan]\n")
    console.print(f"  Total Traces:      {data['total_traces']}")
    console.print(f"    OK:              {data['ok_traces']}")
    console.print(f"    Error:           {data['error_traces']}")
    console.print(f"  Total Spans:       {data['total_spans']}")
    console.print(f"  Input Tokens:      {data['total_input_tokens']:,}")
    console.print(f"  Output Tokens:     {data['total_output_tokens']:,}")
    console.print(f"  Total Cost:        ${data['total_cost_usd']:.6f}")
    console.print(f"  Evaluations:       {data['total_evaluations']}")
    console.print()


@cli.command()
@click.argument("trace_id")
@click.option("--db", type=click.Path(exists=True), default="observatory.db", help="SQLite database path")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
def spans(trace_id: str, db: str, output_format: str) -> None:
    """List spans for a trace."""
    from cli.db import get_trace_spans

    rows = get_trace_spans(trace_id, Path(db))

    if not rows:
        console.print("[yellow]No spans found[/yellow]")
        return

    if output_format == "json":
        import json
        console.print(json.dumps(rows, indent=2, default=str))
    else:
        table = Table(title=f"Spans for {trace_id[:8]}")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Status")
        table.add_column("Duration (ms)", justify="right")
        table.add_column("Tokens", justify="right")
        table.add_column("Cost", justify="right")

        for row in rows:
            duration = ""
            if row.get("start_time") and row.get("end_time"):
                from datetime import datetime
                try:
                    start = datetime.fromisoformat(str(row["start_time"]))
                    end = datetime.fromisoformat(str(row["end_time"]))
                    duration = f"{(end - start).total_seconds() * 1000:.0f}"
                except (ValueError, TypeError):
                    pass

            tokens = ""
            if row.get("tokens_input") or row.get("tokens_output"):
                tokens = f"{row.get('tokens_input', 0)}→{row.get('tokens_output', 0)}"

            table.add_row(
                str(row["id"])[:8],
                str(row["name"]),
                str(row["span_type"]),
                str(row["status"]),
                duration,
                tokens,
                f"${row['cost_usd']:.6f}" if row.get("cost_usd") else "",
            )

        console.print(table)


# ── Server Commands ──────────────────────────────────────────────────


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind to")
@click.option("--port", default=8000, help="Port to bind to")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
@click.option("--db", default="sqlite+aiosqlite:///./observatory.db", help="Database URL")
def serve(host: str, port: int, reload: bool, db: str) -> None:
    """Start the observatory backend server."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]Error: uvicorn is not installed.[/red]")
        console.print("Install it with: pip install 'llm-observatory-backend[sqlite]'")
        return

    os.environ["DATABASE_URL"] = db

    console.print(f"[green]Starting LLM Observatory server on {host}:{port}[/green]")
    console.print(f"[green]Database: {db}[/green]")

    try:
        from app.main import app as fastapi_app
        uvicorn.run(fastapi_app, host=host, port=port, reload=reload)
    except ImportError:
        console.print("[red]Error: backend app module not found.[/red]")
        console.print("Make sure the backend is installed: pip install -e ../backend")
    except (RuntimeError, OSError) as e:
        console.print(f"[red]Server error: {e}[/red]")


@cli.command()
@click.option("--trace-id", required=True, help="Trace ID to evaluate")
@click.option(
    "--evaluator",
    type=click.Choice(["llm_judge", "rule_based", "human"]),
    required=True,
    help="Evaluator type",
)
@click.option("--criteria", help="Comma-separated criteria for evaluation")
@click.option("--endpoint", default="http://localhost:8000", help="API endpoint")
@click.pass_context
def evaluate(
    ctx: click.Context,
    trace_id: str,
    evaluator: str,
    criteria: str | None,
    endpoint: str,
) -> None:
    """Run an evaluation on a trace."""
    import httpx

    console.print(f"[blue]Evaluating trace {trace_id} with {evaluator}...[/blue]")

    criteria_list = criteria.split(",") if criteria else []

    headers: dict[str, str] = {}
    api_key = ctx.obj.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = httpx.post(
            f"{endpoint}/v1/evaluations/run",
            json={
                "trace_id": trace_id,
                "evaluator_type": evaluator,
                "criteria": [{"name": c.strip(), "description": c.strip()} for c in criteria_list] if criteria_list else [],
            },
            headers=headers,
        )
        response.raise_for_status()

        result = response.json()
        console.print("[green]Evaluation completed successfully![/green]")
        console.print(f"Score: {result.get('score', 'N/A')}")
        console.print(f"Passed: {result.get('passed', 'N/A')}")
        if result.get("criteria"):
            console.print("Criteria scores:")
            for k, v in result["criteria"].items():
                console.print(f"  {k}: {v}")
    except httpx.HTTPError as e:
        console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.option("--db", type=click.Path(exists=True), default="observatory.db", help="SQLite database path")
@click.option("--format", "output_format", type=click.Choice(["json"]), default="json", help="Export format")
@click.option("--output", "-o", type=click.Path(), help="Output file (default: stdout)")
@click.option("--status", type=click.Choice(["ok", "error", "unset"]), help="Filter by status")
@click.option("--limit", default=1000, help="Max traces to export")
def export(db: str, output_format: str, output: str | None, status: str | None, limit: int) -> None:
    """Export traces and spans to a file."""
    import json

    from cli.db import get_trace_spans, list_traces

    rows = list_traces(Path(db), limit=limit, status=status)

    if not rows:
        console.print("[yellow]No traces to export[/yellow]")
        return

    traces_data = []
    for row in rows:
        trace_id = str(row["id"])
        spans = get_trace_spans(trace_id, Path(db))
        traces_data.append({
            "id": trace_id,
            "name": row["name"],
            "status": row["status"],
            "session_id": row.get("session_id"),
            "start_time": row.get("start_time"),
            "end_time": row.get("end_time"),
            "metadata": json.loads(row["metadata"]) if row.get("metadata") else None,
            "created_at": row.get("created_at"),
            "spans": spans,
        })

    export_data = {
        "format": "llm-observatory-export",
        "version": "0.1.0",
        "trace_count": len(traces_data),
        "traces": traces_data,
    }

    json_str = json.dumps(export_data, indent=2, default=str)

    if output:
        Path(output).write_text(json_str)
        console.print(f"[green]Exported {len(traces_data)} traces to {output}[/green]")
    else:
        console.print(json_str)


@cli.command()
@click.option("--endpoint", default="http://localhost:8000", help="API endpoint")
@click.pass_context
def status(ctx: click.Context, endpoint: str) -> None:
    """Check the status of the observatory server."""
    import httpx

    headers: dict[str, str] = {}
    api_key = ctx.obj.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = httpx.get(f"{endpoint}/health", headers=headers)
        response.raise_for_status()

        console.print("[green]Server is healthy![/green]")
        console.print(f"Endpoint: {endpoint}")
    except httpx.HTTPError:
        console.print("[red]Server is not responding[/red]")


if __name__ == "__main__":
    cli()
