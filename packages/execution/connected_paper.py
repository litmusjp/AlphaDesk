from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from packages.broker.alpaca_adapter import AlpacaPaperBrokerAdapter
from packages.broker.projections import PostgresBrokerProjectionStore
from packages.broker.reconciliation import BrokerExecutionGate
from packages.database.session import Database
from packages.domain.broker import BrokerOrder
from packages.domain.workflow import OrderIntent, RankedCandidate
from packages.execution.engine import ExecutionBlocked, ExecutionEngine
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
) -> BrokerOrder:
    projections = PostgresBrokerProjectionStore(database.sessions, workspace_id)
    account = await projections.get_account()
    if account is None:
        raise ExecutionBlocked("Broker account projection unavailable")
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
    guardian = PostgresGuardianStore(database.sessions, workspace_id)
    engine = ExecutionEngine(
        adapter,
        PostgresIntentStore(database.sessions, workspace_id),
        preflight=ConnectedPreflight(broker_gate, GuardianExecutionGate(guardian)),
    )
    try:
        return await engine.execute(intent)
    finally:
        await adapter.close()


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
