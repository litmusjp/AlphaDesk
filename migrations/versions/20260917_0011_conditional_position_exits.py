"""Add exact-position conditional exit approvals.

Revision ID: 20260917_0011
Revises: 20260917_0010
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0011"
down_revision: str | None = "20260917_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("conditional_approvals", "opportunity_id", nullable=True)
    op.add_column(
        "conditional_approvals",
        sa.Column("approval_kind", sa.String(length=16), nullable=False, server_default="OPEN"),
    )
    op.add_column(
        "conditional_approvals",
        sa.Column("min_limit_price", sa.Numeric(precision=20, scale=8), nullable=True),
    )
    op.add_column(
        "conditional_approvals",
        sa.Column("position_asset_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "conditional_approvals",
        sa.Column("position_symbol", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "conditional_approvals",
        sa.Column("position_side", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "conditional_approvals",
        sa.Column("exit_order_side", sa.String(length=8), nullable=True),
    )
    for column in ("approval_kind", "position_asset_id", "position_symbol"):
        op.create_index(f"ix_conditional_approvals_{column}", "conditional_approvals", [column])
    op.alter_column("conditional_approvals", "approval_kind", server_default=None)
    op.create_unique_constraint(
        "uq_conditional_exit_position_session",
        "conditional_approvals",
        ["workspace_id", "position_asset_id", "session_date"],
    )


def downgrade() -> None:
    for column in ("approval_kind", "position_asset_id", "position_symbol"):
        op.drop_index(f"ix_conditional_approvals_{column}", table_name="conditional_approvals")
    op.drop_column("conditional_approvals", "exit_order_side")
    op.drop_column("conditional_approvals", "position_side")
    op.drop_column("conditional_approvals", "position_symbol")
    op.drop_column("conditional_approvals", "position_asset_id")
    op.drop_column("conditional_approvals", "min_limit_price")
    op.drop_column("conditional_approvals", "approval_kind")
    op.alter_column("conditional_approvals", "opportunity_id", nullable=False)
