from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from packages.execution.conditional_approval import (
    ApprovalState,
    ConditionalApproval,
    RevalidationDecision,
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
