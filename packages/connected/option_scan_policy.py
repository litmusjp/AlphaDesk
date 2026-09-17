from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from packages.domain.options import EligibilityResult, LiquidityPolicy, OptionContract, OptionType
from packages.options.liquidity import evaluate_contract


class ScanMode(StrEnum):
    PRE_SCAN = "PRE_SCAN"
    EXECUTION = "EXECUTION"


@dataclass(frozen=True)
class OptionScanDiagnostics:
    total_contracts: int
    requested_type_contracts: int
    strict_eligible_contracts: int
    selected_contracts: int
    rejection_counts: dict[str, int]


@dataclass(frozen=True)
class OptionScanSelection:
    selected: tuple[OptionContract, ...]
    diagnostics: OptionScanDiagnostics


def _policy(*, pre_scan: bool, underlying_symbol: str) -> LiquidityPolicy:
    return LiquidityPolicy(
        supported_underlyings=frozenset({underlying_symbol}),
        min_dte=14,
        max_dte=45,
        max_spread_ratio=Decimal("1.00") if pre_scan else Decimal("0.20"),
        min_open_interest=None if pre_scan else 25,
        max_quote_age_seconds=86400 if pre_scan else 120,
        require_greeks=True,
    )


def select_contracts(
    contracts: tuple[OptionContract, ...],
    *,
    underlying_price: Decimal,
    wanted_type: OptionType,
    as_of: datetime,
    mode: ScanMode,
) -> OptionScanSelection:
    underlying_symbol = contracts[0].underlying_symbol if contracts else ""
    strict_policy = _policy(pre_scan=False, underlying_symbol=underlying_symbol)
    selection_policy = _policy(
        pre_scan=mode is ScanMode.PRE_SCAN,
        underlying_symbol=underlying_symbol,
    )
    rejection_counts: Counter[str] = Counter()
    requested_type_contracts = 0
    strict_eligible_contracts = 0
    selected: list[OptionContract] = []

    for contract in contracts:
        if contract.option_type is not wanted_type:
            continue
        requested_type_contracts += 1
        strict_result: EligibilityResult = evaluate_contract(
            contract,
            underlying_price=underlying_price,
            policy=strict_policy,
            as_of=as_of,
        )
        if strict_result.eligible:
            strict_eligible_contracts += 1
        else:
            rejection_counts.update(strict_result.reasons)
        selection_result = evaluate_contract(
            contract,
            underlying_price=underlying_price,
            policy=selection_policy,
            as_of=as_of,
        )
        if selection_result.eligible:
            selected.append(contract)

    diagnostics = OptionScanDiagnostics(
        total_contracts=len(contracts),
        requested_type_contracts=requested_type_contracts,
        strict_eligible_contracts=strict_eligible_contracts,
        selected_contracts=len(selected),
        rejection_counts=dict(sorted(rejection_counts.items())),
    )
    return OptionScanSelection(selected=tuple(selected), diagnostics=diagnostics)
