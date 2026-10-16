"""add dataset runs and run results

Revision ID: 20261017_runs
Revises: 20261016_datasets
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261017_runs"
down_revision: str | None = "20261016_datasets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the run tables."""

    op.create_table(
        "dataset_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("version", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dataset_runs_dataset_id", "dataset_runs", ["dataset_id"])
    op.create_index("ix_dataset_runs_created_at", "dataset_runs", ["created_at"])
    op.create_table(
        "run_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["dataset_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_results_run_id", "run_results", ["run_id"])
    op.create_index("ix_run_results_item_id", "run_results", ["item_id"])


def downgrade() -> None:
    """Drop the run tables."""

    op.drop_index("ix_run_results_item_id", table_name="run_results")
    op.drop_index("ix_run_results_run_id", table_name="run_results")
    op.drop_table("run_results")
    op.drop_index("ix_dataset_runs_created_at", table_name="dataset_runs")
    op.drop_index("ix_dataset_runs_dataset_id", table_name="dataset_runs")
    op.drop_table("dataset_runs")
