"""add alert rules and alert events

Revision ID: 20261015_alerts
Revises: 20260720_manitos
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261015_alerts"
down_revision: str | None = "20260720_manitos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the alerting tables."""

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("comparator", sa.String(length=2), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=True),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=True),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "alert_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("rule_name", sa.String(length=120), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=True),
        sa.Column("comparator", sa.String(length=2), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_events_rule_id", "alert_events", ["rule_id"])
    op.create_index("ix_alert_events_state", "alert_events", ["state"])
    op.create_index("ix_alert_events_fired_at", "alert_events", ["fired_at"])


def downgrade() -> None:
    """Drop the alerting tables."""

    op.drop_index("ix_alert_events_fired_at", table_name="alert_events")
    op.drop_index("ix_alert_events_state", table_name="alert_events")
    op.drop_index("ix_alert_events_rule_id", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_table("alert_rules")
