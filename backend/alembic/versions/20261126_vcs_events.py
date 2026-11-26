"""add vcs_events (a change merged or closed, for the cost per merged change)

Revision ID: 20261126_vcs_events
Revises: 20261112_repetitions
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261126_vcs_events"
down_revision: str | None = "20261112_repetitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """One row per (project, change, event): a CI hook reporting twice changes nothing."""

    op.create_table(
        "vcs_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("change", sa.String(length=128), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("commit", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "change", "event", name="uq_vcs_event"),
    )
    op.create_index("ix_vcs_events_project_id", "vcs_events", ["project_id"])
    op.create_index("ix_vcs_events_commit", "vcs_events", ["commit"])


def downgrade() -> None:
    """Drop the table."""

    op.drop_index("ix_vcs_events_commit", table_name="vcs_events")
    op.drop_index("ix_vcs_events_project_id", table_name="vcs_events")
    op.drop_table("vcs_events")
