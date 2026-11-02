"""index spans by type and start time (governance views)

``/v1/governance`` lists decision, security, approval and skill spans over a time window; without
this index every request scanned the whole spans table.

Revision ID: 20261102_span_type
Revises: 20261019_parent_ref
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261102_span_type"
down_revision: str | None = "20261019_parent_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_spans_span_type_start_time", "spans", ["span_type", "start_time"])


def downgrade() -> None:
    op.drop_index("ix_spans_span_type_start_time", table_name="spans")
