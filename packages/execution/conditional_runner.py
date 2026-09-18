from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from packages.broker.alpaca_adapter import AlpacaPaperBrokerAdapter
from packages.connected.opportunities import ConnectedAnalysis, ConnectedOpportunityService
from packages.connected.option_scan_policy import ScanMode
from packages.database.models import ConnectedOpportunityRecord
from packages.database.session import Database
from packages.domain.broker import BrokerOrder
from packages.domain.workflow import OrderIntent, RankedCandidate
from packages.execution.conditional_approval import (
    ApprovalState,
    ConditionalApproval,
    RevalidationDecision,
    candidate_structure_identity,
    order_structure_fingerprint,
    revalidate_for_submission,
)
from packages.execution.conditional_store import ConditionalApprovalStore
from packages.execution.connected_paper import execute_connected_paper_order
from packages.execution.engine import ExecutionBlocked, SubmissionUncertain
from packages.security.credentials import CredentialCipher
from packages.security.store import CredentialStore


def _open_order_matches(
    order: BrokerOrder,
    intent: OrderIntent,
    *,
    approved_client_order_id: str,
    expected_broker_account_id: str | None = None,
) -> bool:
    return (
        bool(order.broker_order_id)
        and (
            expected_broker_account_id is None
            or (
                order.broker_account_id == expected_broker_account_id
                and order.environment == "PAPER"
            )
        )
        and order.client_order_id == approved_client_order_id
        and intent.client_order_id == approved_client_order_id
        and order.asset_class == "us_option"
        and order.quantity == intent.quantity
        and order.order_type.lower() == "limit"
        and order.order_class.lower() == "mleg"
        and order.time_in_force.lower() == "day"
        and order.limit_price == intent.limit_price
        and len(order.legs) == len(intent.legs)
        and all(
            broker_leg.symbol == intent_leg.symbol
            and broker_leg.side == intent_leg.side
            and broker_leg.quantity == intent_leg.ratio
            for broker_leg, intent_leg in zip(order.legs, intent.legs, strict=True)
        )
    )


def _candidate_matches_intent(candidate: RankedCandidate, intent: OrderIntent) -> bool:
    candidate_legs = tuple(
        (
            leg.contract.symbol,
            "buy" if leg.side.value == "long" else "sell",
            leg.ratio,
        )
        for leg in candidate.structure.legs
    )
    intent_legs = tuple((leg.symbol, leg.side, leg.ratio) for leg in intent.legs)
    return candidate.structure.quantity == intent.quantity and candidate_legs == intent_legs


def _candidate_structure_identity(candidate: RankedCandidate) -> dict[str, object]:
    return candidate_structure_identity(candidate)


def _intent_matches_approved(candidate: OrderIntent, approved: OrderIntent) -> bool:
    return candidate.model_dump(mode="json") == approved.model_dump(mode="json")


def _open_order_state(order: BrokerOrder) -> ApprovalState:
    status = order.status.lower()
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
        return ApprovalState.SUBMISSION_UNCERTAIN
    if status in {"new", "accepted", "pending_new"} and order.filled_quantity != 0:
        return ApprovalState.SUBMISSION_UNCERTAIN
    if order.quantity is not None and order.filled_quantity == order.quantity:
        if status not in {"filled", "canceled", "rejected", "expired", "replaced"}:
            return ApprovalState.SUBMISSION_UNCERTAIN
        return ApprovalState.FILLED
    if status in {"canceled", "rejected", "expired", "replaced"} and order.filled_quantity > 0:
        return ApprovalState.PARTIALLY_FILLED
    return {
        "filled": ApprovalState.FILLED,
        "partially_filled": ApprovalState.PARTIALLY_FILLED,
        "expired": ApprovalState.BROKER_REJECTED,
        "replaced": ApprovalState.BROKER_REJECTED,
        "rejected": ApprovalState.BROKER_REJECTED,
        "canceled": ApprovalState.BROKER_REJECTED,
    }.get(status, ApprovalState.SUBMITTED)


def _open_fill_response_is_valid(order: BrokerOrder) -> bool:
    if (
        order.quantity is None
        or not order.quantity.is_finite()
        or not order.filled_quantity.is_finite()
        or order.filled_quantity < 0
        or order.filled_quantity > order.quantity
    ):
        return False
    status = order.status.lower()
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
    if status in {"new", "accepted", "pending_new"} and order.filled_quantity != 0:
        return False
    if order.filled_average_price is not None and (
        not order.filled_average_price.is_finite() or order.filled_average_price <= 0
    ):
        return False
    if order.filled_quantity > 0 and (
        order.filled_average_price is None
        or not order.filled_average_price.is_finite()
        or order.filled_average_price <= 0
    ):
        return False
    if status == "filled":
        return order.filled_quantity == order.quantity
    if status == "partially_filled":
        return Decimal("0") < order.filled_quantity < order.quantity
    return True


async def process_workspace_approvals(
    *, database: Database, cipher: CredentialCipher, workspace_id: UUID, now: datetime
) -> None:
    store = ConditionalApprovalStore(database)
    now = datetime.now(UTC)
    await store.reclaim_stale_revalidating(workspace_id=workspace_id, now=now)
    await _recover_ready_to_submit(database, store, cipher, workspace_id)
    await store.expire_before(workspace_id=workspace_id, now=now)
    session_date = now.astimezone(ZoneInfo("America/New_York")).date()
    while True:
        now = datetime.now(UTC)
        session_date = now.astimezone(ZoneInfo("America/New_York")).date()
        approval_record = await store.claim_next(
            workspace_id=workspace_id, session_date=session_date, now=now
        )
        if approval_record is None:
            return
        claim_token = approval_record.claim_token
        if claim_token is None:
            await store.mark_unclaimable_recovery(
                workspace_id=workspace_id,
                approval_id=approval_record.approval_id,
                now=now,
            )
            continue
        bound_claim_token = claim_token

        async def _finish(
            approval_id: UUID, _claim_token: UUID = bound_claim_token, **kwargs: Any
        ) -> None:
            await store.finish(
                approval_id,
                workspace_id=workspace_id,
                claim_token=_claim_token,
                **kwargs,
            )

        submission_started = False
        try:
            if approval_record.opportunity_id is None:
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="opportunity_id_missing",
                )
                continue
            async with database.sessions() as session:
                original_record = await session.scalar(
                    select(ConnectedOpportunityRecord).where(
                        ConnectedOpportunityRecord.workspace_id == workspace_id,
                        ConnectedOpportunityRecord.opportunity_id == approval_record.opportunity_id,
                    )
                )
            if original_record is None:
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="opportunity_missing",
                )
                continue
            original = ConnectedAnalysis.model_validate(original_record.payload)
            if (
                approval_record.approved_intent_payload is None
                or approval_record.approved_structure_identity is None
            ):
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="approved_snapshot_missing",
                )
                continue
            approved_intent = OrderIntent.model_validate(approval_record.approved_intent_payload)
            secret = await CredentialStore(database.sessions, cipher).reveal(workspace_id, "ALPACA")
            if secret is None:
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="alpaca_credential_unavailable",
                )
                continue
            service = ConnectedOpportunityService(
                database.sessions,
                workspace_id,
                str(secret["api_key_id"]),
                str(secret["secret_key"]),
            )
            fresh = await service.analyze(
                original.symbol,
                mode=ScanMode.EXECUTION,
                approved_intent=approved_intent,
            )
            if (
                fresh.disposition != "TRADE"
                or fresh.order_intent is None
                or fresh.candidate is None
            ):
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason=f"fresh_disposition_{fresh.disposition.lower()}",
                )
                continue
            fresh_intent = OrderIntent.model_validate(fresh.order_intent)
            fresh_candidate = RankedCandidate.model_validate(fresh.candidate)
            if not _intent_matches_approved(fresh_intent, approved_intent):
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=datetime.now(UTC),
                    reason="approved_intent_changed",
                )
                continue
            if (
                _candidate_structure_identity(fresh_candidate)
                != approval_record.approved_structure_identity
            ):
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=datetime.now(UTC),
                    reason="approved_structure_changed",
                )
                continue
            if not _candidate_matches_intent(fresh_candidate, fresh_intent):
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=datetime.now(UTC),
                    reason="fresh_candidate_intent_mismatch",
                )
                continue
            if fresh_intent.client_order_id != approval_record.client_order_id:
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=datetime.now(UTC),
                    reason="approved_client_order_id_changed",
                )
                continue
            now = datetime.now(UTC)
            session_date = now.astimezone(ZoneInfo("America/New_York")).date()
            approval = ConditionalApproval(
                approval_id=approval_record.approval_id,
                workspace_id=approval_record.workspace_id,
                opportunity_id=approval_record.opportunity_id,
                client_order_id=approval_record.client_order_id,
                session_date=approval_record.session_date,
                approved_at=approval_record.approved_at,
                expires_at=approval_record.expires_at,
                structure_fingerprint=approval_record.structure_fingerprint,
                max_limit_price=approval_record.max_limit_price,
                max_loss=approval_record.max_loss,
                max_quantity=approval_record.max_quantity,
                max_quote_age_seconds=approval_record.max_quote_age_seconds,
                state=ApprovalState.APPROVED_FOR_SESSION,
            )
            if fresh.observed_at > now:
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="quote_timestamp_in_future",
                )
                continue
            leg_quote_times = tuple(
                leg.contract.quote.quoted_at for leg in fresh_candidate.structure.legs
            )
            if not leg_quote_times or any(quoted_at > now for quoted_at in leg_quote_times):
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="option_quote_timestamp_invalid",
                )
                continue
            quote_age_seconds = max(0, ceil((now - min(leg_quote_times)).total_seconds()))
            result = revalidate_for_submission(
                approval,
                now=now,
                session_date=session_date,
                structure_fingerprint=order_structure_fingerprint(fresh_intent),
                limit_price=fresh_intent.limit_price,
                maximum_loss=fresh_candidate.structure.max_loss,
                quantity=fresh_intent.quantity,
                quote_age_seconds=quote_age_seconds,
            )
            if result.decision is not RevalidationDecision.READY_TO_SUBMIT:
                await _finish(
                    approval_record.approval_id,
                    state=(
                        ApprovalState.EXPIRED
                        if result.decision is RevalidationDecision.EXPIRED
                        else ApprovalState.CONDITION_FAILED
                    ),
                    now=now,
                    reason=result.reason,
                )
                continue
            now = datetime.now(UTC)
            if claim_token is None or not await store.authorize_submission(
                approval_record.approval_id,
                workspace_id=workspace_id,
                now=now,
                claim_token=claim_token,
            ):
                continue
            submission_started = True
            broker_order = await execute_connected_paper_order(
                database=database,
                cipher=cipher,
                workspace_id=workspace_id,
                candidate=fresh_candidate,
                intent=fresh_intent,
                approved_broker_account_id=approval_record.approved_broker_account_id,
            )
            if not _open_order_matches(
                broker_order,
                fresh_intent,
                approved_client_order_id=approval_record.client_order_id,
                expected_broker_account_id=approval_record.approved_broker_account_id,
            ):
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.SUBMISSION_UNCERTAIN,
                    now=datetime.now(UTC),
                    reason="submitted_order_identity_mismatch",
                    broker_order_id=broker_order.broker_order_id or None,
                    broker_order=broker_order,
                )
                continue
            if not _open_fill_response_is_valid(broker_order):
                await _finish(
                    approval_record.approval_id,
                    state=ApprovalState.SUBMISSION_UNCERTAIN,
                    now=datetime.now(UTC),
                    reason="broker_fill_response_invalid",
                    broker_order_id=broker_order.broker_order_id or None,
                    broker_order=broker_order,
                )
                continue
            final_state = _open_order_state(broker_order)
            await _finish(
                approval_record.approval_id,
                state=final_state,
                now=now,
                submitted_at=now,
                reason=(
                    broker_order.status if final_state is ApprovalState.BROKER_REJECTED else None
                ),
                broker_order_id=broker_order.broker_order_id,
                broker_order=broker_order,
            )
        except SubmissionUncertain as error:
            await _finish(
                approval_record.approval_id,
                state=ApprovalState.SUBMISSION_UNCERTAIN,
                now=now,
                reason=str(error),
            )
        except ExecutionBlocked as error:
            await _finish(
                approval_record.approval_id,
                state=ApprovalState.CONDITION_FAILED,
                now=now,
                reason=type(error).__name__,
            )
        except Exception as error:
            await _finish(
                approval_record.approval_id,
                state=(
                    ApprovalState.SUBMISSION_UNCERTAIN
                    if submission_started
                    else ApprovalState.CONDITION_FAILED
                ),
                now=now,
                reason=type(error).__name__,
            )


async def _recover_ready_to_submit(
    database: Database,
    store: ConditionalApprovalStore,
    cipher: CredentialCipher,
    workspace_id: UUID,
) -> None:
    records = await store.list_ready_to_submit(workspace_id=workspace_id, approval_kind="OPEN")
    if not records:
        return
    secret = await CredentialStore(database.sessions, cipher).reveal(workspace_id, "ALPACA")
    if secret is None:
        return
    broker = AlpacaPaperBrokerAdapter(str(secret["api_key_id"]), str(secret["secret_key"]))
    try:
        for record in records:
            now = datetime.now(UTC)
            claim_token = record.claim_token
            if claim_token is None:
                await store.mark_unclaimable_recovery(
                    workspace_id=workspace_id,
                    approval_id=record.approval_id,
                    now=now,
                )
                continue
            bound_claim_token = claim_token

            async def _finish(
                approval_id: UUID,
                _claim_token: UUID = bound_claim_token,
                **kwargs: Any,
            ) -> None:
                await store.finish(
                    approval_id,
                    workspace_id=workspace_id,
                    claim_token=_claim_token,
                    **kwargs,
                )

            try:
                order = await broker.get_order(client_order_id=record.client_order_id)
            except Exception:
                continue
            if order is None:
                await _finish(
                    record.approval_id,
                    state=ApprovalState.SUBMISSION_UNCERTAIN,
                    now=now,
                    reason="ready_submission_recovery_no_broker_order",
                )
                continue
            if not _open_fill_response_is_valid(order):
                await _finish(
                    record.approval_id,
                    state=ApprovalState.SUBMISSION_UNCERTAIN,
                    now=now,
                    reason="recovered_broker_fill_response_invalid",
                    broker_order_id=order.broker_order_id or None,
                    broker_order=order,
                )
                continue
            if record.approved_intent_payload is None:
                await _finish(
                    record.approval_id,
                    state=ApprovalState.SUBMISSION_UNCERTAIN,
                    now=now,
                    reason="recovered_approved_snapshot_missing",
                )
                continue
            recovered_intent = OrderIntent.model_validate(record.approved_intent_payload)
            if not _open_order_matches(
                order,
                recovered_intent,
                approved_client_order_id=record.client_order_id,
                expected_broker_account_id=record.approved_broker_account_id,
            ):
                await _finish(
                    record.approval_id,
                    state=ApprovalState.SUBMISSION_UNCERTAIN,
                    now=now,
                    reason="recovered_order_identity_mismatch",
                    broker_order_id=order.broker_order_id or None,
                    broker_order=order,
                )
                continue
            final_state = _open_order_state(order)
            await _finish(
                record.approval_id,
                state=final_state,
                now=now,
                submitted_at=order.submitted_at or now,
                reason=order.status if final_state is ApprovalState.BROKER_REJECTED else None,
                broker_order_id=order.broker_order_id,
                broker_order=order,
            )
    finally:
        await broker.close()
