from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from packages.execution.conditional_approval import (
    ApprovalState,
    ConditionalApproval,
    ConditionalExitApproval,
    ExitOrderSide,
    RevalidationDecision,
    revalidate_exit_for_submission,
    revalidate_for_submission,
)


def approval(**overrides: object) -> ConditionalApproval:
    values = {
        "approval_id": uuid4(),
        "workspace_id": uuid4(),
        "opportunity_id": uuid4(),
        "client_order_id": "ad-test-order",
        "session_date": date(2026, 9, 18),
        "approved_at": datetime(2026, 9, 17, 12, tzinfo=UTC),
        "expires_at": datetime(2026, 9, 19, tzinfo=UTC),
        "structure_fingerprint": "AAPL-20261016-200C-205C",
        "max_limit_price": Decimal("2.20"),
        "max_loss": Decimal("220"),
        "max_quantity": 1,
        "max_quote_age_seconds": 30,
        "state": ApprovalState.APPROVED_FOR_SESSION,
    }
    values.update(overrides)
    return ConditionalApproval(**values)


def test_revalidation_allows_one_approved_session_submission() -> None:
    result = revalidate_for_submission(
        approval(),
        now=datetime(2026, 9, 18, 14, 31, tzinfo=UTC),
        session_date=date(2026, 9, 18),
        structure_fingerprint="AAPL-20261016-200C-205C",
        limit_price=Decimal("2.08"),
        maximum_loss=Decimal("208"),
        quantity=1,
        quote_age_seconds=4,
    )

    assert result.decision is RevalidationDecision.READY_TO_SUBMIT


def test_revalidation_rejects_price_outside_approved_bound() -> None:
    result = revalidate_for_submission(
        approval(),
        now=datetime(2026, 9, 18, 14, 31, tzinfo=UTC),
        session_date=date(2026, 9, 18),
        structure_fingerprint="AAPL-20261016-200C-205C",
        limit_price=Decimal("2.21"),
        maximum_loss=Decimal("221"),
        quantity=1,
        quote_age_seconds=4,
    )

    assert result.decision is RevalidationDecision.CONDITION_FAILED
    assert result.reason == "limit_price_exceeds_approval"


def test_revalidation_rejects_stale_quote_and_wrong_structure() -> None:
    result = revalidate_for_submission(
        approval(),
        now=datetime(2026, 9, 18, 14, 31, tzinfo=UTC),
        session_date=date(2026, 9, 18),
        structure_fingerprint="AAPL-20261016-200C-210C",
        limit_price=Decimal("2.08"),
        maximum_loss=Decimal("208"),
        quantity=1,
        quote_age_seconds=31,
    )

    assert result.decision is RevalidationDecision.CONDITION_FAILED
    assert result.reason in {"structure_changed", "quote_stale"}


def test_revalidation_expires_outside_approved_session() -> None:
    result = revalidate_for_submission(
        approval(),
        now=datetime(2026, 9, 19, 14, 31, tzinfo=UTC),
        session_date=date(2026, 9, 19),
        structure_fingerprint="AAPL-20261016-200C-205C",
        limit_price=Decimal("2.08"),
        maximum_loss=Decimal("208"),
        quantity=1,
        quote_age_seconds=4,
    )

    assert result.decision is RevalidationDecision.EXPIRED
    assert result.reason == "approval_session_mismatch"


def exit_approval(**overrides: object) -> ConditionalExitApproval:
    values = {
        "approval_id": uuid4(),
        "workspace_id": uuid4(),
        "position_asset_id": "asset-qqq-1",
        "position_symbol": "QQQ260918C00500000",
        "position_side": "long",
        "approved_quantity": Decimal("1"),
        "order_side": ExitOrderSide.SELL,
        "session_date": date(2026, 9, 18),
        "approved_at": datetime(2026, 9, 17, 12, tzinfo=UTC),
        "expires_at": datetime(2026, 9, 19, tzinfo=UTC),
        "client_order_id": "ad-exit-test-order",
        "limit_price_bound": Decimal("4.50"),
        "max_quote_age_seconds": 30,
        "state": ApprovalState.APPROVED_FOR_SESSION,
    }
    values.update(overrides)
    return ConditionalExitApproval(**values)


def test_exit_revalidation_allows_long_position_sell_at_or_above_floor() -> None:
    result = revalidate_exit_for_submission(
        exit_approval(),
        now=datetime(2026, 9, 18, 14, 31, tzinfo=UTC),
        session_date=date(2026, 9, 18),
        position_asset_id="asset-qqq-1",
        position_symbol="QQQ260918C00500000",
        position_side="long",
        current_quantity=Decimal("1"),
        order_side=ExitOrderSide.SELL,
        limit_price=Decimal("4.55"),
        quote_age_seconds=4,
    )

    assert result.decision is RevalidationDecision.READY_TO_SUBMIT


def test_exit_revalidation_rejects_position_change_and_wrong_sell_bound() -> None:
    changed_position = revalidate_exit_for_submission(
        exit_approval(),
        now=datetime(2026, 9, 18, 14, 31, tzinfo=UTC),
        session_date=date(2026, 9, 18),
        position_asset_id="asset-qqq-1",
        position_symbol="QQQ260918C00500000",
        position_side="long",
        current_quantity=Decimal("2"),
        order_side=ExitOrderSide.SELL,
        limit_price=Decimal("4.55"),
        quote_age_seconds=4,
    )
    below_floor = revalidate_exit_for_submission(
        exit_approval(),
        now=datetime(2026, 9, 18, 14, 31, tzinfo=UTC),
        session_date=date(2026, 9, 18),
        position_asset_id="asset-qqq-1",
        position_symbol="QQQ260918C00500000",
        position_side="long",
        current_quantity=Decimal("1"),
        order_side=ExitOrderSide.SELL,
        limit_price=Decimal("4.49"),
        quote_age_seconds=4,
    )

    assert changed_position.reason == "position_changed"
    assert below_floor.reason == "exit_limit_below_floor"


def test_exit_revalidation_allows_short_position_buy_at_or_below_ceiling() -> None:
    result = revalidate_exit_for_submission(
        exit_approval(
            position_side="short",
            order_side=ExitOrderSide.BUY,
            limit_price_bound=Decimal("5.00"),
        ),
        now=datetime(2026, 9, 18, 14, 31, tzinfo=UTC),
        session_date=date(2026, 9, 18),
        position_asset_id="asset-qqq-1",
        position_symbol="QQQ260918C00500000",
        position_side="short",
        current_quantity=Decimal("1"),
        order_side=ExitOrderSide.BUY,
        limit_price=Decimal("4.95"),
        quote_age_seconds=4,
    )

    assert result.decision is RevalidationDecision.READY_TO_SUBMIT
