"""Add a distinct claim-generation token for conditional approvals.

Revision ID: 20260918_0012
Revises: 20260917_0011
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_0012"
down_revision: str | None = "20260917_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conditional_approvals",
        sa.Column("approved_intent_payload", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "conditional_approvals",
        sa.Column("approved_structure_identity", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "conditional_approvals",
        sa.Column("approved_broker_account_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "conditional_approvals",
        sa.Column("claim_token", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_conditional_approvals_claim_token",
        "conditional_approvals",
        ["claim_token"],
    )


def downgrade() -> None:
    op.drop_column("conditional_approvals", "approved_structure_identity")
    op.drop_column("conditional_approvals", "approved_intent_payload")
    op.drop_column("conditional_approvals", "approved_broker_account_id")
    op.drop_index("ix_conditional_approvals_claim_token", table_name="conditional_approvals")
    op.drop_column("conditional_approvals", "claim_token")
