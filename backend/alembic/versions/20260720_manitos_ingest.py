"""add versioned integration ingestion contract

Revision ID: 20260720_manitos
Revises: 623c57886e29
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_manitos"
down_revision: str | None = "623c57886e29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add integration correlation fields and retry receipts."""

    with op.batch_alter_table("traces") as batch_op:
        batch_op.alter_column(
            "session_id",
            existing_type=sa.String(length=36),
            type_=sa.String(length=255),
            existing_nullable=True,
        )
        batch_op.add_column(sa.Column("turn_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("project_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("environment", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("service_instance_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("actor_id_hash", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column(
                "schema_version",
                sa.String(length=64),
                nullable=False,
                server_default="observer.trace.v1",
            )
        )
        batch_op.create_index("ix_traces_turn_id", ["turn_id"], unique=False)
        batch_op.create_index("ix_traces_project_id", ["project_id"], unique=False)

    op.create_table(
        "ingestion_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_ingestion_project_key"),
    )
    op.create_index("ix_ingestion_receipts_project_id", "ingestion_receipts", ["project_id"])
    op.create_index("ix_ingestion_receipts_trace_id", "ingestion_receipts", ["trace_id"])


def downgrade() -> None:
    """Remove integration ingestion state."""

    op.drop_index("ix_ingestion_receipts_trace_id", table_name="ingestion_receipts")
    op.drop_index("ix_ingestion_receipts_project_id", table_name="ingestion_receipts")
    op.drop_table("ingestion_receipts")

    with op.batch_alter_table("traces") as batch_op:
        batch_op.drop_index("ix_traces_project_id")
        batch_op.drop_index("ix_traces_turn_id")
        batch_op.drop_column("schema_version")
        batch_op.drop_column("actor_id_hash")
        batch_op.drop_column("service_instance_id")
        batch_op.drop_column("environment")
        batch_op.drop_column("project_id")
        batch_op.drop_column("turn_id")
        batch_op.alter_column(
            "session_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=36),
            existing_nullable=True,
        )
