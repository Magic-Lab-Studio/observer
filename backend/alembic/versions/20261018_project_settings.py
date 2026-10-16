"""add project settings (capture mode)

Revision ID: 20261018_settings
Revises: 20261017_runs
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261018_settings"
down_revision: str | None = "20261017_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the project settings table."""

    op.create_table(
        "project_settings",
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("capture_mode", sa.String(length=10), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("project_id"),
    )


def downgrade() -> None:
    """Drop the project settings table."""

    op.drop_table("project_settings")
