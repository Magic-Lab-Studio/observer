"""observer-migrate: bring the database to the latest schema, from a checkout or an installed wheel.

The backend wheel on PyPI shipped no Alembic migrations (0.3.0): someone who installed it with pip
could not create or upgrade the database without cloning the repository and running
``alembic upgrade head`` in ``backend/``. The wheel now carries them under ``app/_migrations`` and
this command runs them with no ``alembic.ini``.

    observer-migrate                     # DATABASE_URL, or sqlite+aiosqlite:///./observatory.db
    observer-migrate --database-url sqlite+aiosqlite:////var/lib/observer/observatory.db
    observer-migrate --current           # print the database's revision
    observer-migrate --sql               # print the SQL instead of running it
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alembic.config import Config

# The same default as app/db/postgres.py.
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./observatory.db"


def script_location() -> Path:
    """The migrations shipped in the wheel, else the checkout's ``backend/alembic``."""
    app_dir = Path(__file__).resolve().parents[1]
    for candidate in (app_dir / "_migrations", app_dir.parent / "alembic"):
        if (candidate / "env.py").is_file() and (candidate / "versions").is_dir():
            return candidate
    raise SystemExit("observer-migrate: no Alembic migrations found next to the app package")


def build_config(database_url: str) -> Config:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(script_location()))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="observer-migrate", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("revision", nargs="?", default="head", help="target (default: head)")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL)
    parser.add_argument("--current", action="store_true", help="print the current revision")
    parser.add_argument("--sql", action="store_true", help="print the SQL instead of running it")
    args = parser.parse_args(argv)

    from alembic import command

    # env.py takes DATABASE_URL over the config's URL: keep the two the same.
    os.environ["DATABASE_URL"] = args.database_url
    config = build_config(args.database_url)
    if args.current:
        command.current(config)
        return 0
    command.upgrade(config, args.revision, sql=args.sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
