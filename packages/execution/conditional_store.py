from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import AuditRecord, ConditionalApprovalRecord
from packages.database.session import Database
from packages.domain.broker import BrokerOrder
from packages.execution.conditional_approval import ApprovalState


def _finish_transition_allowed(current: ApprovalState, target: ApprovalState) -> bool:
    if current is target:
        return True
    if current in {
        ApprovalState.FILLED,
        ApprovalState.BROKER_REJECTED,
        ApprovalState.EXPIRED,
        ApprovalState.CONDITION_FAILED,
        ApprovalState.REJECTED,
    }:
        return False
    if current is ApprovalState.APPROVED_FOR_SESSION:
        return target in {
            ApprovalState.REVALIDATING,
            ApprovalState.EXPIRED,
            ApprovalState.CONDITION_FAILED,
        }
    if current is ApprovalState.REVALIDATING:
        return target in {
            ApprovalState.READY_TO_SUBMIT,
            ApprovalState.EXPIRED,
            ApprovalState.CONDITION_FAILED,
            ApprovalState.SUBMITTED,
            ApprovalState.PARTIALLY_FILLED,
            ApprovalState.FILLED,
            ApprovalState.BROKER_REJECTED,
            ApprovalState.SUBMISSION_UNCERTAIN,
        }
    if current is ApprovalState.READY_TO_SUBMIT:
        return target in {
            ApprovalState.CONDITION_FAILED,
            ApprovalState.SUBMITTED,
            ApprovalState.PARTIALLY_FILLED,
            ApprovalState.FILLED,
            ApprovalState.BROKER_REJECTED,
            ApprovalState.SUBMISSION_UNCERTAIN,
        }
    if current is ApprovalState.PARTIALLY_FILLED:
        return target in {ApprovalState.FILLED, ApprovalState.BROKER_REJECTED}
    if current is ApprovalState.SUBMITTED:
        return target in {
            ApprovalState.PARTIALLY_FILLED,
            ApprovalState.FILLED,
            ApprovalState.BROKER_REJECTED,
        }
    if current is ApprovalState.SUBMISSION_UNCERTAIN:
        return target in {
            ApprovalState.SUBMITTED,
            ApprovalState.PARTIALLY_FILLED,
            ApprovalState.FILLED,
            ApprovalState.BROKER_REJECTED,
        }
    return False


def _broker_evidence_is_valid(
    record: ConditionalApprovalRecord,
    target: ApprovalState,
    broker_order: BrokerOrder | None,
) -> bool:
    if broker_order is None:
        return False
    if not broker_order.broker_order_id or broker_order.client_order_id != record.client_order_id:
        return False
    if (
        record.approved_broker_account_id is None
        or broker_order.broker_account_id != record.approved_broker_account_id
        or broker_order.environment != "PAPER"
    ):
        return False
    if broker_order.asset_class != "us_option":
        return False
    if broker_order.order_type.lower() != "limit" or broker_order.time_in_force.lower() != "day":
        return False
    expected_order_class = "simple" if record.approval_kind == "CLOSE" else "mleg"
    if broker_order.order_class.lower() != expected_order_class:
        return False
    quantity = broker_order.quantity
    if quantity is None or not quantity.is_finite() or quantity <= 0:
        return False
    if record.approval_kind == "CLOSE":
        if (
            broker_order.symbol != record.position_symbol
            or broker_order.side != record.exit_order_side
            or quantity != Decimal(record.max_quantity)
        ):
            return False
    else:
        if quantity > Decimal(record.max_quantity) or not broker_order.legs:
            return False
        if record.approved_intent_payload is None:
            return False
        try:
            approved_quantity = int(record.approved_intent_payload["quantity"])
        except (KeyError, TypeError, ValueError):
            return False
        if quantity != Decimal(approved_quantity):
            return False
        leg_fingerprints: list[str] = []
        for leg in broker_order.legs:
            leg_quantity = leg.quantity
            if (
                leg_quantity is None
                or not leg_quantity.is_finite()
                or leg_quantity <= 0
                or not leg.filled_quantity.is_finite()
                or leg.filled_quantity < 0
                or leg.filled_quantity > leg_quantity
            ):
                return False
            leg_status = leg.status.lower()
            if leg_status == "filled" and leg.filled_quantity != leg_quantity:
                return False
            if leg_status == "partially_filled" and not (
                Decimal("0") < leg.filled_quantity < leg_quantity
            ):
                return False
            if leg_status in {"new", "accepted", "pending_new"} and leg.filled_quantity != 0:
                return False
            leg_fingerprints.append(f"{leg.symbol}:{leg.side}:{leg_quantity.normalize()}")
        fingerprint = "|".join(leg_fingerprints)
        if fingerprint != record.structure_fingerprint:
            return False
    if broker_order.limit_price is None or not broker_order.limit_price.is_finite():
        return False
    if broker_order.limit_price <= 0:
        return False
    if record.approval_kind == "OPEN":
        if record.approved_intent_payload is None:
            return False
        try:
            approved_limit_price = Decimal(str(record.approved_intent_payload["limit_price"]))
        except (KeyError, TypeError, ValueError):
            return False
        if (
            not approved_limit_price.is_finite()
            or broker_order.limit_price != approved_limit_price
            or broker_order.limit_price > record.max_limit_price
        ):
            return False
    elif record.exit_order_side == "buy":
        if broker_order.limit_price > record.max_limit_price:
            return False
    elif record.exit_order_side == "sell":
        if record.min_limit_price is None or broker_order.limit_price < record.min_limit_price:
            return False
    else:
        return False
    filled = broker_order.filled_quantity
    if not filled.is_finite() or filled < 0 or filled > quantity:
        return False
    if broker_order.filled_average_price is not None and (
        not broker_order.filled_average_price.is_finite() or broker_order.filled_average_price <= 0
    ):
        return False
    if filled > 0 and (
        broker_order.filled_average_price is None
        or not broker_order.filled_average_price.is_finite()
        or broker_order.filled_average_price <= 0
    ):
        return False
    status = broker_order.status.lower()
    if status not in {
        "new",
        "accepted",
        "pending_new",
        "partially_filled",
        "filled",
        "rejected",
        "canceled",
        "expired",
        "replaced",
    }:
        return False
    if status == "filled" and filled != quantity:
        return False
    if status == "partially_filled" and not (Decimal("0") < filled < quantity):
        return False
    if target is ApprovalState.FILLED and filled != quantity:
        return False
    if target is ApprovalState.PARTIALLY_FILLED and not (Decimal("0") < filled < quantity):
        return False
    if target is ApprovalState.BROKER_REJECTED and filled != 0:
        return False
    if target is ApprovalState.SUBMITTED and (
        status not in {"new", "accepted", "pending_new"} or filled != 0
    ):
        return False
    if target is ApprovalState.FILLED and status not in {
        "filled",
        "canceled",
        "rejected",
        "expired",
        "replaced",
    }:
        return False
    if target is ApprovalState.PARTIALLY_FILLED and status not in {
        "partially_filled",
        "canceled",
        "rejected",
        "expired",
        "replaced",
    }:
        return False
    if target is ApprovalState.BROKER_REJECTED and status not in {
        "rejected",
        "canceled",
        "expired",
        "replaced",
    }:
        return False
    return True


class ConditionalApprovalStore:
    REVALIDATION_LEASE = timedelta(minutes=10)

    def __init__(self, database: Database) -> None:
        self._database = database

    async def expire_before(self, *, workspace_id: UUID, now: datetime) -> int:
        async with self._database.sessions.begin() as session:
            records = list(
                await session.scalars(
                    select(ConditionalApprovalRecord)
                    .where(
                        ConditionalApprovalRecord.workspace_id == workspace_id,
                        ConditionalApprovalRecord.state == ApprovalState.APPROVED_FOR_SESSION,
                        ConditionalApprovalRecord.expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            return self._mark_expired(session, records, now)

    async def expire_all_before(self, *, now: datetime) -> int:
        async with self._database.sessions.begin() as session:
            records = list(
                await session.scalars(
                    select(ConditionalApprovalRecord)
                    .where(
                        ConditionalApprovalRecord.state == ApprovalState.APPROVED_FOR_SESSION,
                        ConditionalApprovalRecord.expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            return self._mark_expired(session, records, now)

    async def reclaim_all_stale_revalidating(self, *, now: datetime) -> int:
        cutoff = now - self.REVALIDATION_LEASE
        async with self._database.sessions.begin() as session:
            records = list(
                await session.scalars(
                    select(ConditionalApprovalRecord)
                    .where(
                        ConditionalApprovalRecord.state == ApprovalState.REVALIDATING,
                        (
                            (ConditionalApprovalRecord.claimed_at <= cutoff)
                            | ConditionalApprovalRecord.claimed_at.is_(None)
                            | (ConditionalApprovalRecord.expires_at <= now)
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            count = 0
            for record in records:
                if (
                    record.claimed_at is None
                    or record.claim_token is None
                    or record.expires_at <= now
                ):
                    record.state = ApprovalState.EXPIRED
                    record.failure_reason = (
                        "revalidation_claim_missing"
                        if record.claimed_at is None or record.claim_token is None
                        else "approval_expired"
                    )
                    action = "CONDITIONAL_APPROVAL_EXPIRED"
                else:
                    record.state = ApprovalState.APPROVED_FOR_SESSION
                    record.failure_reason = "revalidation_claim_reclaimed"
                    action = "CONDITIONAL_APPROVAL_RECLAIMED"
                record.claimed_at = None
                record.claim_token = None
                record.updated_at = now
                session.add(
                    AuditRecord(
                        audit_id=uuid4(),
                        workspace_id=record.workspace_id,
                        actor_user_id=None,
                        action=action,
                        detail={"approval_id": str(record.approval_id)},
                        occurred_at=now,
                    )
                )
                count += 1
            return count

    async def reclaim_stale_revalidating(self, *, workspace_id: UUID, now: datetime) -> int:
        cutoff = now - self.REVALIDATION_LEASE
        async with self._database.sessions.begin() as session:
            records = list(
                await session.scalars(
                    select(ConditionalApprovalRecord)
                    .where(
                        ConditionalApprovalRecord.workspace_id == workspace_id,
                        ConditionalApprovalRecord.state == ApprovalState.REVALIDATING,
                        (
                            (ConditionalApprovalRecord.claimed_at <= cutoff)
                            | ConditionalApprovalRecord.claimed_at.is_(None)
                            | (ConditionalApprovalRecord.expires_at <= now)
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for record in records:
                if (
                    record.claimed_at is None
                    or record.claim_token is None
                    or record.expires_at <= now
                ):
                    record.state = ApprovalState.EXPIRED
                    record.failure_reason = (
                        "revalidation_claim_missing"
                        if record.claimed_at is None or record.claim_token is None
                        else "approval_expired"
                    )
                    action = "CONDITIONAL_APPROVAL_EXPIRED"
                else:
                    record.state = ApprovalState.APPROVED_FOR_SESSION
                    record.failure_reason = "revalidation_reclaimed_after_worker_restart"
                    action = "CONDITIONAL_APPROVAL_RECLAIMED"
                record.claimed_at = None
                record.claim_token = None
                record.updated_at = now
                session.add(
                    AuditRecord(
                        audit_id=uuid4(),
                        workspace_id=record.workspace_id,
                        actor_user_id=None,
                        action=action,
                        detail={"approval_id": str(record.approval_id)},
                        occurred_at=now,
                    )
                )
            return len(records)

    @staticmethod
    def _mark_expired(
        session: AsyncSession, records: list[ConditionalApprovalRecord], now: datetime
    ) -> int:
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
        self, *, workspace_id: UUID, session_date: date, now: datetime, approval_kind: str = "OPEN"
    ) -> ConditionalApprovalRecord | None:
        async with self._database.sessions.begin() as session:
            record = await session.scalar(
                select(ConditionalApprovalRecord)
                .where(
                    ConditionalApprovalRecord.workspace_id == workspace_id,
                    ConditionalApprovalRecord.approval_kind == approval_kind,
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
            record.claim_token = uuid4()
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
        workspace_id: UUID,
        state: ApprovalState,
        now: datetime,
        claim_token: UUID,
        reason: str | None = None,
        submitted_at: datetime | None = None,
        broker_order_id: str | None = None,
        broker_order: BrokerOrder | None = None,
    ) -> None:
        async with self._database.sessions.begin() as session:
            record = await session.scalar(
                select(ConditionalApprovalRecord)
                .where(
                    ConditionalApprovalRecord.approval_id == approval_id,
                    ConditionalApprovalRecord.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            if record is None:
                return
            if record.claim_token != claim_token:
                return
            if broker_order_id is None and broker_order is not None:
                broker_order_id = broker_order.broker_order_id or None
            if broker_order is not None and broker_order_id != broker_order.broker_order_id:
                raise ValueError("broker evidence order ID mismatch")
            if (
                record.broker_order_id is not None
                and broker_order_id is not None
                and record.broker_order_id != broker_order_id
            ):
                raise ValueError("broker order identity changed")
            current_state = ApprovalState(record.state)
            if not _finish_transition_allowed(current_state, state):
                return
            if state in {
                ApprovalState.SUBMITTED,
                ApprovalState.PARTIALLY_FILLED,
                ApprovalState.FILLED,
                ApprovalState.BROKER_REJECTED,
            } and not (broker_order_id or record.broker_order_id):
                return
            if state in {
                ApprovalState.SUBMITTED,
                ApprovalState.PARTIALLY_FILLED,
                ApprovalState.FILLED,
                ApprovalState.BROKER_REJECTED,
            } and not _broker_evidence_is_valid(record, state, broker_order):
                return
            record.state = state
            if current_state is not state or reason is not None:
                record.failure_reason = reason
            if submitted_at is not None and record.submitted_at is None:
                record.submitted_at = submitted_at
            if broker_order_id is not None and record.broker_order_id is None:
                record.broker_order_id = broker_order_id
            record.updated_at = now
            broker_detail = (
                {
                    "broker_order_id": broker_order.broker_order_id,
                    "broker_client_order_id": broker_order.client_order_id,
                    "broker_status": broker_order.status,
                    "broker_symbol": broker_order.symbol,
                    "broker_side": broker_order.side,
                    "broker_quantity": str(broker_order.quantity),
                    "broker_filled_quantity": str(broker_order.filled_quantity),
                    "broker_filled_average_price": str(broker_order.filled_average_price),
                    "broker_limit_price": str(broker_order.limit_price),
                }
                if broker_order is not None
                else None
            )
            session.add(
                AuditRecord(
                    audit_id=uuid4(),
                    workspace_id=record.workspace_id,
                    actor_user_id=None,
                    action=f"CONDITIONAL_APPROVAL_{state.value}",
                    detail={
                        "approval_id": str(record.approval_id),
                        "reason": reason,
                        "broker_evidence": broker_detail,
                    },
                    occurred_at=now,
                )
            )

    async def authorize_submission(
        self, approval_id: UUID, *, workspace_id: UUID, now: datetime, claim_token: UUID
    ) -> bool:
        """Atomically authorize a claimed approval immediately before broker submission."""
        async with self._database.sessions.begin() as session:
            record = await session.scalar(
                select(ConditionalApprovalRecord)
                .where(
                    ConditionalApprovalRecord.approval_id == approval_id,
                    ConditionalApprovalRecord.workspace_id == workspace_id,
                    ConditionalApprovalRecord.claim_token == claim_token,
                )
                .with_for_update()
            )
            if record is None or record.state != ApprovalState.REVALIDATING:
                return False
            effective_now = datetime.now(UTC)
            if record.expires_at <= effective_now:
                record.state = ApprovalState.EXPIRED
                record.failure_reason = "approval_expired"
                record.updated_at = effective_now
                session.add(
                    AuditRecord(
                        audit_id=uuid4(),
                        workspace_id=record.workspace_id,
                        actor_user_id=None,
                        action="CONDITIONAL_APPROVAL_EXPIRED",
                        detail={"approval_id": str(record.approval_id)},
                        occurred_at=effective_now,
                    )
                )
                return False
            record.state = ApprovalState.READY_TO_SUBMIT
            record.updated_at = effective_now
            session.add(
                AuditRecord(
                    audit_id=uuid4(),
                    workspace_id=record.workspace_id,
                    actor_user_id=None,
                    action="CONDITIONAL_APPROVAL_READY_TO_SUBMIT",
                    detail={"approval_id": str(record.approval_id)},
                    occurred_at=effective_now,
                )
            )
            return True

    async def list_ready_to_submit(
        self, *, workspace_id: UUID, approval_kind: str
    ) -> list[ConditionalApprovalRecord]:
        async with self._database.sessions() as session:
            return list(
                await session.scalars(
                    select(ConditionalApprovalRecord)
                    .where(
                        ConditionalApprovalRecord.workspace_id == workspace_id,
                        ConditionalApprovalRecord.approval_kind == approval_kind,
                        ConditionalApprovalRecord.state.in_(
                            [ApprovalState.READY_TO_SUBMIT, ApprovalState.SUBMISSION_UNCERTAIN]
                        ),
                    )
                    .order_by(ConditionalApprovalRecord.created_at)
                )
            )

    async def mark_unclaimable_recovery(
        self, *, workspace_id: UUID, approval_id: UUID, now: datetime
    ) -> None:
        async with self._database.sessions.begin() as session:
            record = await session.scalar(
                select(ConditionalApprovalRecord)
                .where(
                    ConditionalApprovalRecord.workspace_id == workspace_id,
                    ConditionalApprovalRecord.approval_id == approval_id,
                    ConditionalApprovalRecord.state.in_(
                        [ApprovalState.READY_TO_SUBMIT, ApprovalState.SUBMISSION_UNCERTAIN]
                    ),
                )
                .with_for_update()
            )
            if record is None:
                return
            record.state = ApprovalState.SUBMISSION_UNCERTAIN
            record.failure_reason = "recovery_claim_missing"
            record.updated_at = now
            session.add(
                AuditRecord(
                    audit_id=uuid4(),
                    workspace_id=workspace_id,
                    actor_user_id=None,
                    action="CONDITIONAL_APPROVAL_RECOVERY_UNCLAIMABLE",
                    detail={"approval_id": str(approval_id)},
                    occurred_at=now,
                )
            )
