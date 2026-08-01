"""Tests for LLM Observatory CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cli.db import (
    get_connection,
    get_stats,
    get_trace,
    get_trace_spans,
    list_evaluations,
    list_traces,
)
from cli.main import cli
from click.testing import CliRunner

# ── Helpers ──────────────────────────────────────────────────────────

def _seed_db(db: Path, *, traces: int = 3, spans_per_trace: int = 2) -> dict:
    """Insert test data and return summary."""
    conn = get_connection(db)
    trace_ids = []
    for i in range(traces):
        tid = f"trace-{i:04d}"
        conn.execute(
            "INSERT INTO traces (id, name, status, start_time, end_time, created_at, session_id) "
            "VALUES (?, ?, 'ok', '2025-01-01T00:00:00', '2025-01-01T00:00:01', ?, ?)",
            (tid, f"test-trace-{i}", f"2025-01-01T00:00:0{i}", f"session-{i}"),
        )
        trace_ids.append(tid)
        for j in range(spans_per_trace):
            span_id = f"span-{i:04d}-{j:02d}"
            conn.execute(
                "INSERT INTO spans (id, trace_id, name, span_type, start_time, end_time, "
                "status, tokens_input, tokens_output, cost_usd) "
                "VALUES (?, ?, ?, 'llm_call', '2025-01-01T00:00:00', '2025-01-01T00:00:01', 'ok', ?, ?, ?)",
                (span_id, tid, f"span-{j}", 100 + j, 200 + j, 0.001 * (j + 1)),
            )
    # Add one error trace
    conn.execute(
        "INSERT INTO traces (id, name, status, start_time, end_time, created_at) "
        "VALUES ('trace-err', 'error-trace', 'error', '2025-01-01T00:00:00', '2025-01-01T00:00:01', '2025-01-01T00:00:02')"
    )
    conn.execute(
        "INSERT INTO spans (id, trace_id, name, span_type, start_time, end_time, status, tokens_input, tokens_output, cost_usd) "
        "VALUES ('span-err', 'trace-err', 'fail-span', 'llm_call', '2025-01-01T00:00:00', '2025-01-01T00:00:01', 'error', 50, 10, 0.005)"
    )
    # Add evaluation
    conn.execute(
        "INSERT INTO evaluations (id, trace_id, evaluator_type, score, created_at) "
        "VALUES ('eval-1', 'trace-0000', 'rule_based', 0.85, '2025-01-01T00:00:03')"
    )
    conn.commit()
    conn.close()
    return {"traces": traces + 1, "total_spans": traces * spans_per_trace + 1}


@pytest.fixture()
def db_file(tmp_path: Path) -> Path:
    """Create a seeded database in a temp directory."""
    db = tmp_path / "test.db"
    _seed_db(db)
    return db


@pytest.fixture()
def empty_db(tmp_path: Path) -> Path:
    """Create an empty (but schema-initialized) database."""
    db = tmp_path / "empty.db"
    get_connection(db).close()
    return db


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ── db.py unit tests ─────────────────────────────────────────────────

class TestDBHelpers:
    def test_get_connection_creates_schema(self, tmp_path: Path) -> None:
        db = tmp_path / "new.db"
        conn = get_connection(db)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "traces" in tables
        assert "spans" in tables
        assert "evaluations" in tables

    def test_get_connection_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "idempotent.db"
        get_connection(db).close()
        get_connection(db).close()

    def test_list_traces_empty(self, empty_db: Path) -> None:
        assert list_traces(empty_db) == []

    def test_list_traces_default(self, db_file: Path) -> None:
        rows = list_traces(db_file)
        assert len(rows) == 4  # 3 ok + 1 error

    def test_list_traces_limit(self, db_file: Path) -> None:
        rows = list_traces(db_file, limit=2)
        assert len(rows) == 2

    def test_list_traces_status_filter(self, db_file: Path) -> None:
        rows = list_traces(db_file, status="error")
        assert len(rows) == 1
        assert rows[0]["name"] == "error-trace"

    def test_list_traces_name_search(self, db_file: Path) -> None:
        rows = list_traces(db_file, name_search="test-trace")
        assert len(rows) == 3

    def test_list_traces_name_search_no_match(self, db_file: Path) -> None:
        rows = list_traces(db_file, name_search="nonexistent")
        assert rows == []

    def test_get_trace_found(self, db_file: Path) -> None:
        t = get_trace("trace-0000", db_file)
        assert t is not None
        assert t["name"] == "test-trace-0"
        assert t["status"] == "ok"

    def test_get_trace_not_found(self, db_file: Path) -> None:
        assert get_trace("no-such-id", db_file) is None

    def test_get_trace_spans(self, db_file: Path) -> None:
        spans = get_trace_spans("trace-0000", db_file)
        assert len(spans) == 2
        assert spans[0]["trace_id"] == "trace-0000"

    def test_get_trace_spans_empty(self, db_file: Path) -> None:
        spans = get_trace_spans("trace-err", db_file)
        assert len(spans) == 1

    def test_list_evaluations(self, db_file: Path) -> None:
        evals = list_evaluations(db_file)
        assert len(evals) == 1
        assert evals[0]["evaluator_type"] == "rule_based"

    def test_list_evaluations_filter_by_trace(self, db_file: Path) -> None:
        evals = list_evaluations(db_file, trace_id="trace-0000")
        assert len(evals) == 1

    def test_list_evaluations_filter_no_match(self, db_file: Path) -> None:
        evals = list_evaluations(db_file, trace_id="trace-nope")
        assert evals == []

    def test_get_stats(self, db_file: Path) -> None:
        stats = get_stats(db_file)
        assert stats["total_traces"] == 4
        assert stats["ok_traces"] == 3
        assert stats["error_traces"] == 1
        assert stats["total_spans"] == 7
        assert stats["total_evaluations"] == 1
        assert stats["total_input_tokens"] > 0
        assert stats["total_output_tokens"] > 0
        assert stats["total_cost_usd"] > 0

    def test_get_stats_empty(self, empty_db: Path) -> None:
        stats = get_stats(empty_db)
        assert stats["total_traces"] == 0
        assert stats["total_spans"] == 0
        assert stats["total_input_tokens"] == 0
        assert stats["total_cost_usd"] == 0.0


# ── CLI command tests ────────────────────────────────────────────────

class TestCLIHelp:
    def test_version(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["--version"])
        assert r.exit_code == 0
        assert "0.1.1" in r.output

    def test_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["--help"])
        assert r.exit_code == 0
        assert "LLM Observatory CLI" in r.output

    def test_traces_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["traces", "--help"])
        assert r.exit_code == 0
        assert "--limit" in r.output

    def test_inspect_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["inspect", "--help"])
        assert r.exit_code == 0
        assert "TRACE_ID" in r.output

    def test_stats_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["stats", "--help"])
        assert r.exit_code == 0

    def test_spans_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["spans", "--help"])
        assert r.exit_code == 0
        assert "--format" in r.output

    def test_export_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["export", "--help"])
        assert r.exit_code == 0
        assert "--output" in r.output

    def test_serve_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["serve", "--help"])
        assert r.exit_code == 0
        assert "--reload" in r.output

    def test_evaluate_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["evaluate", "--help"])
        assert r.exit_code == 0
        assert "--trace-id" in r.output

    def test_status_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["status", "--help"])
        assert r.exit_code == 0
        assert "--endpoint" in r.output


class TestTracesCommand:
    def test_traces_shows_table(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["traces", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "test-trace-0" in r.output
        assert "Traces (4 shown)" in r.output

    def test_traces_empty(self, runner: CliRunner, empty_db: Path) -> None:
        r = runner.invoke(cli, ["traces", "--db", str(empty_db)])
        assert r.exit_code == 0
        assert "No traces found" in r.output

    def test_traces_limit(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["traces", "--db", str(db_file), "--limit", "1"])
        assert r.exit_code == 0
        assert "Traces (1 shown)" in r.output

    def test_traces_filter_status(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["traces", "--db", str(db_file), "--status", "error"])
        assert r.exit_code == 0
        assert "error-trace" in r.output

    def test_traces_search(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["traces", "--db", str(db_file), "--search", "error"])
        assert r.exit_code == 0
        assert "error-trace" in r.output
        assert "Traces (1 shown)" in r.output

    def test_traces_missing_db(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["traces", "--db", "/nonexistent/path.db"])
        assert r.exit_code != 0


class TestInspectCommand:
    def test_inspect_found(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["inspect", "trace-0000", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "test-trace-0" in r.output
        assert "Spans (2)" in r.output

    def test_inspect_not_found(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["inspect", "no-such", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "not found" in r.output

    def test_inspect_shows_status(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["inspect", "trace-err", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "error" in r.output


class TestStatsCommand:
    def test_stats_output(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["stats", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "Total Traces:" in r.output
        assert "4" in r.output
        assert "OK:" in r.output
        assert "3" in r.output
        assert "Error:" in r.output

    def test_stats_empty(self, runner: CliRunner, empty_db: Path) -> None:
        r = runner.invoke(cli, ["stats", "--db", str(empty_db)])
        assert r.exit_code == 0
        assert "Total Traces:" in r.output
        assert "0" in r.output

    def test_stats_shows_tokens(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["stats", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "Input Tokens:" in r.output
        assert "Output Tokens:" in r.output
        assert "Total Cost:" in r.output


class TestSpansCommand:
    def test_spans_table(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["spans", "trace-0000", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "span-0" in r.output
        assert "span-1" in r.output

    def test_spans_json(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["spans", "trace-0000", "--db", str(db_file), "--format", "json"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert len(data) == 2
        assert data[0]["trace_id"] == "trace-0000"

    def test_spans_empty(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["spans", "trace-0000", "--db", str(db_file)])
        assert r.exit_code == 0

    def test_spans_no_match(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["spans", "no-such-trace", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "No spans found" in r.output

    def test_spans_shows_duration(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["spans", "trace-0000", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "Duration (ms)" in r.output

    def test_spans_shows_tokens(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["spans", "trace-0000", "--db", str(db_file)])
        assert r.exit_code == 0
        assert "Tokens" in r.output


class TestExportCommand:
    def test_export_stdout(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["export", "--db", str(db_file)])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["format"] == "llm-observatory-export"
        assert data["version"] == "0.1.1"
        assert data["trace_count"] == 4

    def test_export_to_file(self, runner: CliRunner, db_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "export.json"
        r = runner.invoke(cli, ["export", "--db", str(db_file), "-o", str(out)])
        assert r.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["trace_count"] == 4

    def test_export_empty(self, runner: CliRunner, empty_db: Path) -> None:
        r = runner.invoke(cli, ["export", "--db", str(empty_db)])
        assert r.exit_code == 0
        assert "No traces to export" in r.output

    def test_export_filter_status(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["export", "--db", str(db_file), "--status", "error"])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["trace_count"] == 1
        assert data["traces"][0]["status"] == "error"

    def test_export_includes_spans(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["export", "--db", str(db_file)])
        data = json.loads(r.output)
        trace_with_spans = next(t for t in data["traces"] if t["id"] == "trace-0000")
        assert len(trace_with_spans["spans"]) == 2

    def test_export_limit(self, runner: CliRunner, db_file: Path) -> None:
        r = runner.invoke(cli, ["export", "--db", str(db_file), "--limit", "2"])
        data = json.loads(r.output)
        assert data["trace_count"] == 2

    def test_export_roundtrip(self, runner: CliRunner, db_file: Path, tmp_path: Path) -> None:
        """Export then re-import preserves data structure."""
        out = tmp_path / "roundtrip.json"
        r = runner.invoke(cli, ["export", "--db", str(db_file), "-o", str(out)])
        assert r.exit_code == 0
        data = json.loads(out.read_text())
        for trace in data["traces"]:
            assert "id" in trace
            assert "name" in trace
            assert "spans" in trace
            for span in trace["spans"]:
                assert "trace_id" in span
                assert "name" in span


class TestEvaluateCommand:
    def test_evaluate_help_shows_options(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["evaluate", "--help"])
        assert r.exit_code == 0
        assert "llm_judge" in r.output
        assert "rule_based" in r.output
        assert "human" in r.output

    def test_evaluate_requires_trace_id(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["evaluate", "--evaluator", "rule_based"])
        assert r.exit_code != 0

    def test_evaluate_requires_evaluator(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["evaluate", "--trace-id", "t1"])
        assert r.exit_code != 0


class TestStatusCommand:
    def test_status_server_down(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["status", "--endpoint", "http://127.0.0.1:19999"])
        assert r.exit_code == 0
        assert "not responding" in r.output


class TestAPIKeyOption:
    def test_api_key_from_option(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["--api-key", "test-key", "status", "--endpoint", "http://127.0.0.1:19999"])
        assert r.exit_code == 0
        assert "not responding" in r.output

    def test_api_key_none_by_default(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["status", "--endpoint", "http://127.0.0.1:19999"])
        assert r.exit_code == 0


class TestImportCommand:
    def test_import_help(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["import", "--help"])
        assert r.exit_code == 0
        assert "Dry run" in r.output or "dry-run" in r.output

    def test_import_file_not_found(self, runner: CliRunner, tmp_path: Path) -> None:
        r = runner.invoke(cli, ["import", str(tmp_path / "nonexistent.json")])
        assert r.exit_code != 0

    def test_import_dry_run(self, runner: CliRunner, tmp_path: Path) -> None:
        export_file = tmp_path / "export.json"
        export_data = {
            "format": "llm-observatory-export",
            "version": "0.1.0",
            "trace_count": 2,
            "traces": [
                {
                    "id": "imp-001",
                    "name": "imported-trace-1",
                    "status": "ok",
                    "session_id": "s1",
                    "start_time": "2025-06-01T00:00:00",
                    "end_time": "2025-06-01T00:00:01",
                    "created_at": "2025-06-01T00:00:00",
                    "spans": [
                        {"id": "sp-001", "name": "llm-call", "span_type": "llm",
                         "start_time": "2025-06-01T00:00:00", "end_time": "2025-06-01T00:00:01",
                         "status": "ok", "tokens_input": 100, "tokens_output": 50,
                         "cost_usd": 0.001},
                    ],
                },
                {
                    "id": "imp-002",
                    "name": "imported-trace-2",
                    "status": "error",
                    "spans": [],
                },
            ],
        }
        export_file.write_text(json.dumps(export_data))

        r = runner.invoke(cli, ["import", str(export_file), "--dry-run"])
        assert r.exit_code == 0
        assert "imported-trace-1" in r.output
        assert "Dry run" in r.output

        # Nothing should be written
        db = tmp_path / "test.db"
        conn = get_connection(db)
        cursor = conn.execute("SELECT COUNT(*) FROM traces")
        assert cursor.fetchone()[0] == 0
        conn.close()

    def test_import_writes_to_db(self, runner: CliRunner, tmp_path: Path) -> None:
        export_file = tmp_path / "export.json"
        export_data = {
            "format": "llm-observatory-export",
            "version": "0.1.0",
            "trace_count": 1,
            "traces": [
                {
                    "id": "imp-100",
                    "name": "imported-ok",
                    "status": "ok",
                    "session_id": "s1",
                    "start_time": "2025-06-01T00:00:00",
                    "end_time": "2025-06-01T00:00:01",
                    "created_at": "2025-06-01T00:00:00",
                    "spans": [
                        {"id": "sp-100", "name": "call-1", "span_type": "llm",
                         "start_time": "2025-06-01T00:00:00", "end_time": "2025-06-01T00:00:01",
                         "status": "ok", "tokens_input": 200, "tokens_output": 100,
                         "cost_usd": 0.005},
                    ],
                },
            ],
        }
        export_file.write_text(json.dumps(export_data))

        db = tmp_path / "import.db"
        r = runner.invoke(cli, ["import", str(export_file), "--db", str(db)])
        assert r.exit_code == 0
        assert "Imported 1 traces" in r.output

        # Verify data was written
        conn = get_connection(db)
        cursor = conn.execute("SELECT COUNT(*) FROM traces")
        assert cursor.fetchone()[0] == 1
        cursor = conn.execute("SELECT name FROM traces WHERE id='imp-100'")
        assert cursor.fetchone()[0] == "imported-ok"
        cursor = conn.execute("SELECT COUNT(*) FROM spans WHERE trace_id='imp-100'")
        assert cursor.fetchone()[0] == 1
        conn.close()

    def test_import_empty_file(self, runner: CliRunner, tmp_path: Path) -> None:
        export_file = tmp_path / "empty.json"
        export_file.write_text(json.dumps({"format": "llm-observatory-export", "traces": []}))
        r = runner.invoke(cli, ["import", str(export_file)])
        assert r.exit_code == 0
        assert "No traces" in r.output
