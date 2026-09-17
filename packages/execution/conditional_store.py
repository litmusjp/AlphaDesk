from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from packages.database.models import AuditRecord, ConditionalApprovalRecord
from packages.database.session import Database
from packages.execution.conditional_approval import ApprovalState


class ConditionalApprovalStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def expire_before(self, *, workspace_id: UUID, now: datetime) -> int:
        async with self._database.sessions.begin() as session:
            records = list(
                await session.scalars(
                    select(ConditionalApprovalRecord).where(
                        ConditionalApprovalRecord.workspace_id == workspace_id,
                        ConditionalApprovalRecord.state == ApprovalState.APPROVED_FOR_SESSION,
                        ConditionalApprovalRecord.expires_at <= now,
                    )
                )
            )
            for record in records:
                record.state = ApprovalState.EXPIRED
                record.failure_reason = "approval_expired"
                record.updated_at = now
                session.add(
                    AuditRecord(
                        audit_id=uuid4(),
                        workspace_id=record.workspace_id,
                        actor_user_id=None,
                        action="CONDITIONAL_APPROVAL_EXPIRED",
                        detail={"approval_id": str(record.approval_id)},
                        occurred_at=now,
                    )
                )
            return len(records)

    async def claim_next(
        self, *, workspace_id: UUID, session_date: date, now: datetime
    ) -> ConditionalApprovalRecord | None:
        async with self._database.sessions.begin() as session:
            record = await session.scalar(
                select(ConditionalApprovalRecord)
                .where(
                    ConditionalApprovalRecord.workspace_id == workspace_id,
                    ConditionalApprovalRecord.session_date == session_date,
                    ConditionalApprovalRecord.state == ApprovalState.APPROVED_FOR_SESSION,
                    ConditionalApprovalRecord.expires_at > now,
                )
                .order_by(ConditionalApprovalRecord.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if record is None:
                return None
            record.state = ApprovalState.REVALIDATING
            record.claimed_at = now
            record.updated_at = now
            session.add(
                AuditRecord(
                    audit_id=uuid4(),
                    workspace_id=record.workspace_id,
                    actor_user_id=None,
                    action="CONDITIONAL_APPROVAL_REVALIDATING",
                    detail={"approval_id": str(record.approval_id)},
                    occurred_at=now,
                )
            )
            return record

    async def finish(
        self,
        approval_id: UUID,
        *,
        state: ApprovalState,
        now: datetime,
        reason: str | None = None,
        submitted_at: datetime | None = None,
        broker_order_id: str | None = None,
    ) -> None:
        async with self._database.sessions.begin() as session:
            record = await session.get(
                ConditionalApprovalRecord, approval_id, with_for_update=True
            )
            if record is None:
                return
            record.state = state
            record.failure_reason = reason
            record.submitted_at = submitted_at
            record.broker_order_id = broker_order_id
            record.updated_at = now
            session.add(
                AuditRecord(
                    audit_id=uuid4(),
                    workspace_id=record.workspace_id,
                    actor_user_id=None,
                    action=f"CONDITIONAL_APPROVAL_{state.value}",
                    detail={"approval_id": str(record.approval_id), "reason": reason},
                    occurred_at=now,
                )
            )
