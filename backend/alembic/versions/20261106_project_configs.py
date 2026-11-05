"""add project configuration sections (groundedness first)

Revision ID: 20261106_configs
Revises: 20261102_span_type
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261106_configs"
down_revision: str | None = "20261102_span_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the project configuration table (one row per project and section)."""

    op.create_table(
        "project_configs",
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("section", sa.String(length=40), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("project_id", "section"),
    )


def downgrade() -> None:
    """Drop the project configuration table."""

    op.drop_table("project_configs")
