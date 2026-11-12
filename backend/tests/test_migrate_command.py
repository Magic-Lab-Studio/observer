"""observer-migrate: the database reaches the latest schema without alembic.ini.

0.3.0's backend wheel shipped no Alembic migrations, so a pip install could not create or
upgrade its database; the wheel now carries them."""

import sqlite3
from pathlib import Path

from alembic.script import ScriptDirectory

from app.ops import migrate

BACKEND = Path(__file__).resolve().parents[1]


def test_migrate_creates_a_fresh_database_at_head(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db = tmp_path / "observatory.db"
    url = f"sqlite+aiosqlite:///{db}"
    assert migrate.main(["--database-url", url]) == 0
    head = ScriptDirectory.from_config(migrate.build_config(url)).get_current_head()
    with sqlite3.connect(db) as conn:
        assert conn.execute("select version_num from alembic_version").fetchone()[0] == head
        rows = conn.execute("select name from sqlite_master where type='table'")
        tables = {row[0] for row in rows}
    assert {"traces", "spans"} <= tables
    assert migrate.main(["--database-url", url]) == 0  # a second run is a no-op


def test_the_wheel_carries_every_migration():
    # Read as text: tomllib is 3.11+ and CI also runs on 3.10.
    text = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.hatch.build.targets.wheel.force-include]" in text
    for line in (
        '"alembic/env.py" = "app/_migrations/env.py"',
        '"alembic/script.py.mako" = "app/_migrations/script.py.mako"',
        '"alembic/versions" = "app/_migrations/versions"',
        'observer-migrate = "app.ops.migrate:main"',
    ):
        assert line in text, line
