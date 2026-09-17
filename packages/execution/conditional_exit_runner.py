from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from packages.broker.alpaca_adapter import AlpacaPaperBrokerAdapter
from packages.database.models import ConditionalApprovalRecord
from packages.database.session import Database
from packages.execution.conditional_approval import (
    ApprovalState,
    ConditionalExitApproval,
    ExitOrderSide,
    RevalidationDecision,
    revalidate_exit_for_submission,
)
from packages.execution.conditional_store import ConditionalApprovalStore
from packages.guardian.gate import GuardianExecutionGate
from packages.guardian.store import PostgresGuardianStore
from packages.options.alpaca_adapter import AlpacaOptionChainAdapter
from packages.security.credentials import CredentialCipher
from packages.security.store import CredentialStore


async def process_workspace_exit_approvals(
    *, database: Database, cipher: CredentialCipher, workspace_id: UUID, now: datetime
) -> None:
    store = ConditionalApprovalStore(database)
    await store.expire_before(workspace_id=workspace_id, now=now)
    session_date = now.astimezone(ZoneInfo("America/New_York")).date()
    secret = await CredentialStore(database.sessions, cipher).reveal(workspace_id, "ALPACA")
    if secret is None:
        return

    while True:
        record = await store.claim_next(
            workspace_id=workspace_id,
            session_date=session_date,
            now=now,
            approval_kind="CLOSE",
        )
        if record is None:
            return
        try:
            await _process_claimed_exit(
                database=database,
                store=store,
                secret=secret,
                record=record,
                workspace_id=workspace_id,
                session_date=session_date,
                now=now,
            )
        except Exception as error:
            await store.finish(
                record.approval_id,
                state=ApprovalState.SUBMISSION_UNCERTAIN,
                now=now,
                reason=type(error).__name__,
            )


async def _process_claimed_exit(
    *,
    database: Database,
    store: ConditionalApprovalStore,
    secret: dict[str, object],
    record: ConditionalApprovalRecord,
    workspace_id: UUID,
    session_date: date,
    now: datetime,
) -> None:
    if (
        record.position_asset_id is None
        or record.position_symbol is None
        or record.position_side is None
        or record.exit_order_side is None
    ):
        await store.finish(
            record.approval_id,
            state=ApprovalState.CONDITION_FAILED,
            now=now,
            reason="exit_identity_incomplete",
        )
        return
    try:
        order_side = ExitOrderSide(record.exit_order_side)
    except ValueError:
        await store.finish(
            record.approval_id,
            state=ApprovalState.CONDITION_FAILED,
            now=now,
            reason="exit_side_invalid",
        )
        return
    bound = record.min_limit_price if order_side is ExitOrderSide.SELL else record.max_limit_price
    if bound is None or bound <= 0:
        await store.finish(
            record.approval_id,
            state=ApprovalState.CONDITION_FAILED,
            now=now,
            reason="exit_limit_bound_missing",
        )
        return

    broker = AlpacaPaperBrokerAdapter(str(secret["api_key_id"]), str(secret["secret_key"]))
    try:
        existing = await broker.get_order(client_order_id=record.client_order_id)
        if existing is not None:
            await store.finish(
                record.approval_id,
                state=ApprovalState.SUBMITTED,
                now=now,
                submitted_at=record.submitted_at or now,
                broker_order_id=existing.broker_order_id,
            )
            return
        snapshot = await broker.reconcile()
    finally:
        await broker.close()

    account = snapshot.account
    if (
        account.status.upper() != "ACTIVE"
        or account.trading_blocked
        or account.account_blocked
        or account.trade_suspended_by_user
    ):
        await store.finish(
            record.approval_id,
            state=ApprovalState.CONDITION_FAILED,
            now=now,
            reason="broker_account_unavailable",
        )
        return
    allowed, guardian_reason = await GuardianExecutionGate(
        PostgresGuardianStore(database.sessions, workspace_id)
    ).execution_allowed()
    if not allowed:
        await store.finish(
            record.approval_id,
            state=ApprovalState.CONDITION_FAILED,
            now=now,
            reason=f"guardian_blocked:{guardian_reason}",
        )
        return

    position = next(
        (
            item
            for item in snapshot.positions
            if item.asset_id == record.position_asset_id and item.symbol == record.position_symbol
        ),
        None,
    )
    if position is None:
        await store.finish(
            record.approval_id,
            state=ApprovalState.CONDITION_FAILED,
            now=now,
            reason="position_missing",
        )
        return

    option_data = AlpacaOptionChainAdapter(str(secret["api_key_id"]), str(secret["secret_key"]))
    quote = await option_data.get_latest_quote(record.position_symbol)
    quoted_at = quote.quoted_at
    if quoted_at.tzinfo is None:
        quoted_at = quoted_at.replace(tzinfo=UTC)
    quote_age_seconds = max(0, int((now - quoted_at).total_seconds()))
    limit_price = quote.bid if order_side is ExitOrderSide.SELL else quote.ask
    if limit_price <= 0:
        await store.finish(
            record.approval_id,
            state=ApprovalState.CONDITION_FAILED,
            now=now,
            reason="quote_not_executable",
        )
        return

    approval = ConditionalExitApproval(
        approval_id=record.approval_id,
        workspace_id=record.workspace_id,
        position_asset_id=record.position_asset_id,
        position_symbol=record.position_symbol,
        position_side=record.position_side,
        approved_quantity=Decimal(record.max_quantity),
        order_side=order_side,
        session_date=record.session_date,
        approved_at=record.approved_at,
        expires_at=record.expires_at,
        client_order_id=record.client_order_id,
        limit_price_bound=bound,
        max_quote_age_seconds=record.max_quote_age_seconds,
        state=ApprovalState.APPROVED_FOR_SESSION,
    )
    result = revalidate_exit_for_submission(
        approval,
        now=now,
        session_date=session_date,
        position_asset_id=position.asset_id,
        position_symbol=position.symbol,
        position_side=position.side,
        current_quantity=position.quantity,
        order_side=order_side,
        limit_price=limit_price,
        quote_age_seconds=quote_age_seconds,
    )
    if result.decision is not RevalidationDecision.READY_TO_SUBMIT:
        await store.finish(
            record.approval_id,
            state=(
                ApprovalState.EXPIRED
                if result.decision is RevalidationDecision.EXPIRED
                else ApprovalState.CONDITION_FAILED
            ),
            now=now,
            reason=result.reason,
        )
        return

    broker = AlpacaPaperBrokerAdapter(str(secret["api_key_id"]), str(secret["secret_key"]))
    try:
        order = await broker.submit_close_limit(
            symbol=record.position_symbol,
            quantity=record.max_quantity,
            limit_price=str(limit_price),
            order_side=order_side,
            client_order_id=record.client_order_id,
        )
    finally:
        await broker.close()
    status = order.status.lower()
    final_state = {
        "filled": ApprovalState.FILLED,
        "partially_filled": ApprovalState.PARTIALLY_FILLED,
        "rejected": ApprovalState.BROKER_REJECTED,
        "canceled": ApprovalState.BROKER_REJECTED,
    }.get(status, ApprovalState.SUBMITTED)
    await store.finish(
        record.approval_id,
        state=final_state,
        now=now,
        submitted_at=now,
        reason=order.status if final_state is ApprovalState.BROKER_REJECTED else None,
        broker_order_id=order.broker_order_id,
    )
