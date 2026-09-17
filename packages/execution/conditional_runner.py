from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from packages.connected.opportunities import ConnectedAnalysis, ConnectedOpportunityService
from packages.connected.option_scan_policy import ScanMode
from packages.database.models import ConnectedOpportunityRecord
from packages.database.session import Database
from packages.domain.workflow import OrderIntent, RankedCandidate
from packages.execution.conditional_approval import (
    ApprovalState,
    ConditionalApproval,
    RevalidationDecision,
    order_structure_fingerprint,
    revalidate_for_submission,
)
from packages.execution.conditional_store import ConditionalApprovalStore
from packages.execution.connected_paper import execute_connected_paper_order
from packages.execution.engine import ExecutionBlocked
from packages.security.credentials import CredentialCipher
from packages.security.store import CredentialStore


async def process_workspace_approvals(
    *, database: Database, cipher: CredentialCipher, workspace_id: UUID, now: datetime
) -> None:
    store = ConditionalApprovalStore(database)
    await store.expire_before(workspace_id=workspace_id, now=now)
    session_date = now.astimezone(ZoneInfo("America/New_York")).date()
    while True:
        approval_record = await store.claim_next(
            workspace_id=workspace_id, session_date=session_date, now=now
        )
        if approval_record is None:
            return
        try:
            async with database.sessions() as session:
                original_record = await session.scalar(
                    select(ConnectedOpportunityRecord).where(
                        ConnectedOpportunityRecord.workspace_id == workspace_id,
                        ConnectedOpportunityRecord.opportunity_id
                        == approval_record.opportunity_id,
                    )
                )
            if original_record is None:
                await store.finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="opportunity_missing",
                )
                continue
            original = ConnectedAnalysis.model_validate(original_record.payload)
            if original.order_intent is None:
                await store.finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="original_order_intent_missing",
                )
                continue
            original_intent = OrderIntent.model_validate(original.order_intent)
            secret = await CredentialStore(database.sessions, cipher).reveal(workspace_id, "ALPACA")
            if secret is None:
                await store.finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason="alpaca_credential_unavailable",
                )
                continue
            fresh = await ConnectedOpportunityService(
                database.sessions,
                workspace_id,
                str(secret["api_key_id"]),
                str(secret["secret_key"]),
            ).analyze(original.symbol, mode=ScanMode.EXECUTION)
            if (
                fresh.disposition != "TRADE"
                or fresh.order_intent is None
                or fresh.candidate is None
            ):
                await store.finish(
                    approval_record.approval_id,
                    state=ApprovalState.CONDITION_FAILED,
                    now=now,
                    reason=f"fresh_disposition_{fresh.disposition.lower()}",
                )
                continue
            fresh_intent = OrderIntent.model_validate(fresh.order_intent)
            fresh_candidate = RankedCandidate.model_validate(fresh.candidate)
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
            quote_age_seconds = max(0, int((now - fresh.observed_at).total_seconds()))
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
                await store.finish(
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
            broker_order = await execute_connected_paper_order(
                database=database,
                cipher=cipher,
                workspace_id=workspace_id,
                candidate=fresh_candidate,
                intent=original_intent,
            )
            broker_status = broker_order.status.lower()
            final_state = {
                "filled": ApprovalState.FILLED,
                "partially_filled": ApprovalState.PARTIALLY_FILLED,
                "rejected": ApprovalState.BROKER_REJECTED,
                "canceled": ApprovalState.BROKER_REJECTED,
            }.get(broker_status, ApprovalState.SUBMITTED)
            await store.finish(
                approval_record.approval_id,
                state=final_state,
                now=now,
                submitted_at=now,
                reason=(
                    broker_order.status
                    if final_state is ApprovalState.BROKER_REJECTED
                    else None
                ),
                broker_order_id=broker_order.broker_order_id,
            )
        except ExecutionBlocked as error:
            await store.finish(
                approval_record.approval_id,
                state=ApprovalState.BROKER_REJECTED,
                now=now,
                reason=type(error).__name__,
            )
        except Exception as error:
            await store.finish(
                approval_record.approval_id,
                state=ApprovalState.SUBMISSION_UNCERTAIN,
                now=now,
                reason=type(error).__name__,
            )
