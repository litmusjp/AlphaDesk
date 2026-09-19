from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.data.enums import DataFeed

from packages.connected.opportunities import (
    ConnectedOpportunityService,
    _maybe_create_order_intent,
    _scan_disposition,
)
from packages.connected.option_scan_policy import ScanMode


class StockClientStub:
    def __init__(self) -> None:
        self.snapshot_request: Any = None
        self.bars_request: Any = None

    def get_stock_snapshot(self, request: Any) -> dict[str, Any]:
        self.snapshot_request = request
        stock = SimpleNamespace(
            latest_trade=SimpleNamespace(price=101),
            daily_bar=SimpleNamespace(open=100, volume=1000),
            previous_daily_bar=SimpleNamespace(close=99),
        )
        index = SimpleNamespace(
            latest_trade=SimpleNamespace(price=101),
            daily_bar=SimpleNamespace(open=100),
        )
        return {"AAPL": stock, "SPY": index, "QQQ": index}

    def get_stock_bars(self, request: Any) -> Any:
        self.bars_request = request
        return SimpleNamespace(data={"AAPL": [SimpleNamespace(volume=1000)]})


class NewsClientStub:
    def get_news(self, request: Any) -> list[Any]:
        return []


def test_connected_stock_requests_explicitly_use_iex_feed() -> None:
    stock = StockClientStub()
    service = ConnectedOpportunityService.__new__(ConnectedOpportunityService)
    service._stock = stock
    service._news = NewsClientStub()

    service._features("AAPL")

    assert stock.snapshot_request.feed is DataFeed.IEX
    assert stock.bars_request.feed is DataFeed.IEX


def test_non_intent_scan_never_creates_order_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "packages.connected.opportunities.create_order_intent",
        lambda *_args, **_kwargs: pytest.fail("research must not create an order intent"),
    )

    result = _maybe_create_order_intent(
        SimpleNamespace(decision="APPROVE"),
        SimpleNamespace(),
        mode=ScanMode.PRE_SCAN,
        create_intent=True,
    )

    assert result is None


def test_pre_scan_approved_candidate_is_reviewable_without_intent() -> None:
    assert (
        _scan_disposition(
            mode=ScanMode.PRE_SCAN,
            create_intent=True,
            risk_decision="APPROVE",
            intent=None,
        )
        == "PRE_SCAN_CANDIDATE"
    )


def test_pre_scan_without_intent_remains_research_only() -> None:
    assert (
        _scan_disposition(
            mode=ScanMode.PRE_SCAN,
            create_intent=False,
            risk_decision="APPROVE",
            intent=None,
        )
        == "RESEARCH_CANDIDATE"
    )


def test_execution_rejection_is_not_promoted_to_candidate() -> None:
    assert (
        _scan_disposition(
            mode=ScanMode.EXECUTION,
            create_intent=True,
            risk_decision="REJECT",
            intent=None,
        )
        == "RISK_REJECTED"
    )


def test_rejected_risk_with_intent_remains_rejected() -> None:
    assert (
        _scan_disposition(
            mode=ScanMode.EXECUTION,
            create_intent=True,
            risk_decision="REJECT",
            intent=SimpleNamespace(),
        )
        == "RISK_REJECTED"
    )
