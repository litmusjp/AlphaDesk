"""Add conditional next-session approvals.

Revision ID: 20260917_0010
Revises: 20260903_0009
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260917_0010"
down_revision: str | None = "20260903_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conditional_approvals",
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_order_id", sa.String(length=64), nullable=False),
        sa.Column("structure_fingerprint", sa.String(length=512), nullable=False),
        sa.Column("max_limit_price", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("max_loss", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("max_quantity", sa.Integer(), nullable=False),
        sa.Column("max_quote_age_seconds", sa.Integer(), nullable=False),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["connected_opportunities.opportunity_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["app_users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("approval_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "opportunity_id",
            name="uq_conditional_approval_opportunity",
        ),
        sa.UniqueConstraint("client_order_id", name="uq_conditional_approval_client_order"),
    )
    for column in (
        "workspace_id",
        "opportunity_id",
        "state",
        "session_date",
        "approved_at",
        "expires_at",
        "claimed_at",
        "submitted_at",
        "created_at",
        "updated_at",
        "broker_order_id",
    ):
        op.create_index(f"ix_conditional_approvals_{column}", "conditional_approvals", [column])
    op.create_index(
        "ix_conditional_approvals_client_order_id", "conditional_approvals", ["client_order_id"]
    )


def downgrade() -> None:
    op.drop_table("conditional_approvals")
