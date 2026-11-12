"""add run_results.repetition (the same case run N times in one run)

Revision ID: 20261112_repetitions
Revises: 20261106_configs
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261112_repetitions"
down_revision: str | None = "20261106_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Each result says which repetition of its case it is; existing results are repetition 0."""

    with op.batch_alter_table("run_results") as batch:
        batch.add_column(
            sa.Column("repetition", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    """Drop the repetition column."""

    with op.batch_alter_table("run_results") as batch:
        batch.drop_column("repetition")
