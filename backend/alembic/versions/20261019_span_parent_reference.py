"""spans.parent_span_id becomes a reference without a foreign key

A child span finishes, and is exported, before its parent; OpenTelemetry parents may also live in
another service. With SQLite foreign keys enforced (2026-10-17) the self-referencing constraint
rejected every child that arrived first (HTTP 500), and the OTLP receiver dropped such parents.
Integrity stays with ``spans.trace_id -> traces.id``; the index on ``parent_span_id`` is kept.

Revision ID: 20261019_parent_ref
Revises: 20261018_settings
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261019_parent_ref"
down_revision: str | None = "20261018_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
_SQLITE_NAME = "fk_spans_parent_span_id_spans"


def _parent_fk_names() -> list[str | None]:
    inspector = sa.inspect(op.get_bind())
    return [
        fk.get("name")
        for fk in inspector.get_foreign_keys("spans")
        if fk.get("constrained_columns") == ["parent_span_id"]
    ]


def upgrade() -> None:
    names = _parent_fk_names()
    if not names:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("spans", naming_convention=_CONVENTION) as batch:
            batch.drop_constraint(_SQLITE_NAME, type_="foreignkey")
        return
    for name in names:
        op.drop_constraint(name, "spans", type_="foreignkey")


def downgrade() -> None:
    if _parent_fk_names():
        return
    # References to parents that never arrived cannot satisfy the restored constraint.
    op.execute(
        "UPDATE spans SET parent_span_id = NULL WHERE parent_span_id IS NOT NULL "
        "AND parent_span_id NOT IN (SELECT id FROM spans)"
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("spans", naming_convention=_CONVENTION) as batch:
            batch.create_foreign_key(_SQLITE_NAME, "spans", ["parent_span_id"], ["id"])
        return
    op.create_foreign_key(
        "spans_parent_span_id_fkey", "spans", "spans", ["parent_span_id"], ["id"]
    )
