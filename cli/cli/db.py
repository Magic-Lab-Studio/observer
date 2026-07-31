"""Database helper for local SQLite access."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("observatory.db")

# DDL to create tables if the database is fresh/empty
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    session_id TEXT,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'unset',
    metadata JSON,
    created_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_traces_session_id ON traces (session_id);

CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    parent_span_id TEXT REFERENCES spans(id),
    name TEXT NOT NULL,
    span_type TEXT NOT NULL DEFAULT 'generic',
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'unset',
    input JSON,
    output JSON,
    tokens_input INTEGER,
    tokens_output INTEGER,
    cost_usd NUMERIC(10, 6),
    metadata JSON,
    attributes JSON
);
CREATE INDEX IF NOT EXISTS ix_spans_trace_id ON spans (trace_id);
CREATE INDEX IF NOT EXISTS ix_spans_parent_span_id ON spans (parent_span_id);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    span_id TEXT REFERENCES spans(id) ON DELETE CASCADE,
    evaluator_type TEXT NOT NULL,
    score NUMERIC(5, 4),
    criteria JSON,
    result JSON,
    created_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_evaluations_trace_id ON evaluations (trace_id);
CREATE INDEX IF NOT EXISTS ix_evaluations_span_id ON evaluations (span_id);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a SQLite connection and ensure schema exists."""
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


def list_traces(
    db_path: Path | None = None,
    limit: int = 100,
    offset: int = 0,
    status: str | None = None,
    name_search: str | None = None,
) -> list[dict[str, Any]]:
    """List traces from local SQLite database."""
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM traces WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)

        if name_search:
            query += " AND name LIKE ?"
            params.append(f"%{name_search}%")

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_trace(trace_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Get a single trace by ID."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_trace_spans(trace_id: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Get all spans for a trace."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time",
            (trace_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def list_evaluations(
    db_path: Path | None = None,
    trace_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List evaluations from local SQLite database."""
    conn = get_connection(db_path)
    try:
        query = "SELECT * FROM evaluations WHERE 1=1"
        params: list[Any] = []

        if trace_id:
            query += " AND trace_id = ?"
            params.append(trace_id)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_stats(db_path: Path | None = None) -> dict[str, Any]:
    """Get summary statistics from the database."""
    conn = get_connection(db_path)
    try:
        stats: dict[str, Any] = {}

        # Trace counts
        cursor = conn.execute("SELECT COUNT(*) FROM traces")
        stats["total_traces"] = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM traces WHERE status = 'ok'")
        stats["ok_traces"] = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM traces WHERE status = 'error'")
        stats["error_traces"] = cursor.fetchone()[0]

        # Span counts
        cursor = conn.execute("SELECT COUNT(*) FROM spans")
        stats["total_spans"] = cursor.fetchone()[0]

        # Token totals
        cursor = conn.execute(
            "SELECT SUM(tokens_input), SUM(tokens_output) FROM spans WHERE tokens_input IS NOT NULL"
        )
        row = cursor.fetchone()
        stats["total_input_tokens"] = row[0] or 0
        stats["total_output_tokens"] = row[1] or 0

        # Cost total
        cursor = conn.execute(
            "SELECT SUM(cost_usd) FROM spans WHERE cost_usd IS NOT NULL"
        )
        stats["total_cost_usd"] = cursor.fetchone()[0] or 0.0

        # Evaluation counts
        cursor = conn.execute("SELECT COUNT(*) FROM evaluations")
        stats["total_evaluations"] = cursor.fetchone()[0]

        return stats
    finally:
        conn.close()
