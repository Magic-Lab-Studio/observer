"""add datasets and dataset items

Revision ID: 20261016_datasets
Revises: 20261015_alerts
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261016_datasets"
down_revision: str | None = "20261015_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the dataset tables."""

    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"])
    op.create_table(
        "dataset_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("source_trace_id", sa.String(length=36), nullable=True),
        sa.Column("intent", sa.String(length=80), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("expect", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(length=10), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("observed", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dataset_items_dataset_id", "dataset_items", ["dataset_id"])


def downgrade() -> None:
    """Drop the dataset tables."""

    op.drop_index("ix_dataset_items_dataset_id", table_name="dataset_items")
    op.drop_table("dataset_items")
    op.drop_index("ix_datasets_project_id", table_name="datasets")
    op.drop_table("datasets")
