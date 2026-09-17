from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.connected.option_scan_policy import ScanMode, select_contracts
from packages.domain.options import Greeks, OptionContract, OptionQuote, OptionType

AS_OF = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)


def contract(*, quoted_at: datetime, option_type: OptionType = OptionType.CALL) -> OptionContract:
    return OptionContract(
        contract_id="contract-1",
        symbol="AAPL261016C00200000",
        underlying_symbol="AAPL",
        expiration=AS_OF.date() + timedelta(days=30),
        strike=Decimal("200"),
        option_type=option_type,
        multiplier=100,
        tradable=True,
        quote=OptionQuote(
            bid=Decimal("2.00"),
            ask=Decimal("2.20"),
            bid_size=Decimal("10"),
            ask_size=Decimal("10"),
            quoted_at=quoted_at,
            open_interest=100,
            greeks=Greeks(
                delta=Decimal("0.5"),
                gamma=Decimal("0.1"),
                theta=Decimal("-0.1"),
                vega=Decimal("0.2"),
            ),
        ),
    )


def test_pre_scan_keeps_stale_quote_for_review_but_execution_rejects_it() -> None:
    stale = contract(quoted_at=AS_OF - timedelta(hours=8))

    pre_scan = select_contracts(
        (stale,),
        underlying_price=Decimal("200"),
        wanted_type=OptionType.CALL,
        as_of=AS_OF,
        mode=ScanMode.PRE_SCAN,
    )
    execution = select_contracts(
        (stale,),
        underlying_price=Decimal("200"),
        wanted_type=OptionType.CALL,
        as_of=AS_OF,
        mode=ScanMode.EXECUTION,
    )

    assert len(pre_scan.selected) == 1
    assert len(execution.selected) == 0
    assert execution.diagnostics.rejection_counts["stale_quote"] == 1
    assert pre_scan.diagnostics.strict_eligible_contracts == 0
