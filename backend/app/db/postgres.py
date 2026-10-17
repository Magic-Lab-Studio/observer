"""Database configuration - supports both PostgreSQL and SQLite."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import create_async_engine

# Support both PostgreSQL and SQLite
# PostgreSQL: postgresql+asyncpg://user:pass@host:port/db
# SQLite: sqlite+aiosqlite:///./path/to/db.db
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./observatory.db",
)

# Create engine with appropriate settings
if DATABASE_URL.startswith("sqlite"):
    # SQLite-specific settings
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 5.0},
    )

    from sqlalchemy import event as _event

    @_event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
        # The ondelete=CASCADE foreign keys were never enforced (orphan evaluations after DELETE
        # /v1/traces/{id}) and the
        # default rollback journal blocked dashboard reads during ingestion (audit 2026-10-17).
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        if ":memory:" not in DATABASE_URL:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
else:
    # PostgreSQL settings
    engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
