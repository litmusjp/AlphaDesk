from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from packages.broker.alpaca_adapter import AlpacaPaperBrokerAdapter
from packages.broker.projections import PostgresBrokerProjectionStore
from packages.broker.reconciliation import BrokerExecutionGate
from packages.database.session import Database
from packages.domain.broker import BrokerOrder
from packages.domain.workflow import OrderIntent, RankedCandidate
from packages.execution.engine import ExecutionBlocked, ExecutionEngine, SubmissionUncertain
from packages.execution.store import PostgresIntentStore
from packages.guardian.gate import GuardianExecutionGate
from packages.guardian.store import PostgresGuardianStore
from packages.risk.engine import RiskContext, RiskEngine, RiskPolicy
from packages.security.credentials import CredentialCipher
from packages.security.store import CredentialStore


async def execute_connected_paper_order(
    *,
    database: Database,
    cipher: CredentialCipher,
    workspace_id: UUID,
    candidate: RankedCandidate,
    intent: OrderIntent,
    approved_broker_account_id: str | None = None,
) -> BrokerOrder:
    try:
        return await _execute_connected_paper_order(
            database=database,
            cipher=cipher,
            workspace_id=workspace_id,
            candidate=candidate,
            intent=intent,
            approved_broker_account_id=approved_broker_account_id,
        )
    except (ExecutionBlocked, SubmissionUncertain):
        raise
    except Exception as error:
        raise ExecutionBlocked("pre-submit execution preparation failed") from error


async def _execute_connected_paper_order(
    *,
    database: Database,
    cipher: CredentialCipher,
    workspace_id: UUID,
    candidate: RankedCandidate,
    intent: OrderIntent,
    approved_broker_account_id: str | None = None,
) -> BrokerOrder:
    projections = PostgresBrokerProjectionStore(database.sessions, workspace_id)
    account = await projections.get_account()
    if account is None:
        raise ExecutionBlocked("Broker account projection unavailable")
    if approved_broker_account_id is not None and account.account_id != approved_broker_account_id:
        raise ExecutionBlocked("Broker account changed since approval")
    if (
        account.status.upper() != "ACTIVE"
        or account.trading_blocked
        or account.account_blocked
        or account.trade_suspended_by_user
    ):
        raise ExecutionBlocked("Broker account unavailable")
    positions = await projections.list_positions()
    broker_gate = BrokerExecutionGate(projections)
    gate = await broker_gate.evaluate()
    rerisk = RiskEngine(RiskPolicy()).evaluate(
        candidate,
        RiskContext(
            paper_equity=account.equity,
            open_planned_loss=0,
            underlying_open_risk=0,
            daily_loss=max(account.last_equity - account.equity, Decimal("0")),
            drawdown_percent=max(account.last_equity - account.equity, Decimal("0"))
            / max(account.last_equity, Decimal("1"))
            * Decimal("100"),
            concurrent_option_structures=len(positions),
            broker_execution_allowed=gate.allowed,
        ),
    )
    if rerisk.decision != "APPROVE":
        raise ExecutionBlocked("Deterministic risk no longer approves this order")
    secrets = await CredentialStore(database.sessions, cipher).reveal(workspace_id, "ALPACA")
    if secrets is None:
        raise ExecutionBlocked("Alpaca credential unavailable")
    adapter = AlpacaPaperBrokerAdapter(str(secrets["api_key_id"]), str(secrets["secret_key"]))
    try:
        authenticated_account = await adapter.get_account()
    except Exception as error:
        raise ExecutionBlocked("Authenticated paper account unavailable") from error
    if (
        authenticated_account.account_id != account.account_id
        or (
            approved_broker_account_id is not None
            and authenticated_account.account_id != approved_broker_account_id
        )
        or authenticated_account.status.upper() != "ACTIVE"
        or authenticated_account.trading_blocked
        or authenticated_account.account_blocked
        or authenticated_account.trade_suspended_by_user
    ):
        raise ExecutionBlocked("Authenticated paper account changed or unavailable")
    guardian = PostgresGuardianStore(database.sessions, workspace_id)
    engine = ExecutionEngine(
        adapter,
        PostgresIntentStore(database.sessions, workspace_id),
        preflight=ConnectedPreflight(broker_gate, GuardianExecutionGate(guardian)),
    )
    order: BrokerOrder | None = None
    try:
        order = await engine.execute(
            intent,
            expected_broker_account_id=account.account_id,
        )
    except SubmissionUncertain:
        raise
    finally:
        try:
            await adapter.close()
        except Exception as error:
            if order is not None:
                raise SubmissionUncertain("post-submit adapter cleanup uncertain") from error
    if order is None:
        raise ExecutionBlocked("Order execution returned no broker order")
    return order


class ConnectedPreflight:
    def __init__(self, broker: BrokerExecutionGate, guardian: GuardianExecutionGate) -> None:
        self._broker = broker
        self._guardian = guardian

    async def execution_allowed(self) -> tuple[bool, str]:
        guardian_allowed, guardian_reason = await self._guardian.execution_allowed()
        if not guardian_allowed:
            return False, guardian_reason
        broker = await self._broker.evaluate()
        return broker.allowed, broker.reason
