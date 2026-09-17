from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.ai.provider import AIProvider
from packages.domain.ai import Citation

DISCOVERY_UNIVERSE: tuple[str, ...] = (
    "AAPL",
    "AMZN",
    "AMD",
    "AVGO",
    "BAC",
    "COIN",
    "COST",
    "CRM",
    "DIS",
    "GOOG",
    "GOOGL",
    "HD",
    "IBM",
    "INTC",
    "JNJ",
    "JPM",
    "LLY",
    "MA",
    "MCD",
    "META",
    "MRK",
    "MSFT",
    "MU",
    "NFLX",
    "NVDA",
    "ORCL",
    "PLTR",
    "QCOM",
    "QQQ",
    "SMH",
    "SPY",
    "TLT",
    "TSLA",
    "UNH",
    "V",
    "WMT",
    "XLE",
)


class WatchlistRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
    action: Literal["KEEP", "DROP", "WATCH"]
    rank: int = Field(ge=1, le=25)
    rationale: str = Field(min_length=1, max_length=600)
    option_assessment: Literal[
        "EXECUTION_ELIGIBLE", "REVIEW_ONLY", "NOT_ELIGIBLE", "INSUFFICIENT_DATA"
    ]
    option_reason: str = Field(min_length=1, max_length=400)
    risks: tuple[str, ...] = Field(min_length=1, max_length=6)
    confidence: float = Field(ge=0, le=1)
    citations: tuple[Citation, ...] = Field(min_length=1, max_length=8)


class WatchlistResearchReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1, max_length=1200)
    limitations: tuple[str, ...] = Field(min_length=1, max_length=8)
    recommendations: tuple[WatchlistRecommendation, ...] = Field(min_length=1, max_length=20)
    as_of: datetime


WATCHLIST_RESEARCH_PROMPT = """You are AlphaDesk's read-only watchlist research analyst.

Use only the supplied deterministic scanner evidence. Do not invent prices, news,
financial facts, catalysts, or data freshness. Do not use tools. Do not create
orders, trade intents, approvals, position actions, price targets, or execution
instructions. This is advisory research only.

Rank only symbols in the supplied universe and return no more than 20 recommendations.
For each ranked symbol, choose KEEP, DROP, or WATCH. KEEP means it merits
remaining on the user's watchlist; DROP means it is less useful for this watchlist
based on the evidence; WATCH means evidence is
insufficient or mixed and it should remain under observation. Explain the reasoning
and list concrete risks. Also classify the supplied deterministic option evidence as
EXECUTION_ELIGIBLE, REVIEW_ONLY, NOT_ELIGIBLE, or INSUFFICIENT_DATA. Never call an
option execution eligible unless the supplied evidence explicitly shows a current
strictly eligible contract and passed risk decision; otherwise use a safer label.
This classification is commentary, not authorization. Cite only the exact source_id
values supplied in the input.
State limitations whenever evidence is stale, unavailable, incomplete, or based on
scanner signals rather than fundamental research. Return only the required schema.
"""


async def run_watchlist_research(
    provider: AIProvider,
    *,
    symbols: tuple[str, ...],
    evidence: tuple[dict[str, object], ...],
) -> WatchlistResearchReport:
    universe = tuple(symbol.upper() for symbol in symbols)
    evidence_by_symbol = {
        str(item["symbol"]).upper(): item
        for item in evidence
        if item.get("symbol") is not None
    }
    allowed_source_ids = frozenset(
        str(item["source_id"])
        for item in evidence
        if item.get("status") == "AVAILABLE"
    )
    result = await provider.generate(
        agent_name="watchlist_research",
        instructions=WATCHLIST_RESEARCH_PROMPT,
        input_payload=json.dumps(
            {"universe": universe, "sources": evidence},
            sort_keys=True,
            separators=(",", ":"),
        ),
        response_model=WatchlistResearchReport,
    )
    unknown_symbols = {item.symbol for item in result.recommendations} - set(universe)
    if unknown_symbols:
        raise ValueError(f"watchlist research returned unknown symbols: {sorted(unknown_symbols)}")
    recommendation_symbols = [item.symbol for item in result.recommendations]
    if len(recommendation_symbols) != len(set(recommendation_symbols)):
        raise ValueError("watchlist research returned duplicate symbols")
    recommendation_ranks = [item.rank for item in result.recommendations]
    if set(recommendation_ranks) != set(range(1, len(recommendation_ranks) + 1)):
        raise ValueError("watchlist research returned non-contiguous ranks")
    unknown_sources = {
        citation.source_id
        for item in result.recommendations
        for citation in item.citations
    } - allowed_source_ids
    if unknown_sources:
        raise ValueError(f"watchlist research cited unknown sources: {sorted(unknown_sources)}")
    for item in result.recommendations:
        source = evidence_by_symbol.get(item.symbol)
        if any(citation.source_id != f"scan-{item.symbol}" for citation in item.citations):
            raise ValueError(f"watchlist research cited a source unrelated to {item.symbol}")
        if source is None or source.get("status") != "AVAILABLE":
            if item.option_assessment != "INSUFFICIENT_DATA":
                raise ValueError(
                    f"watchlist research overstated unavailable data for {item.symbol}"
                )
            continue
        diagnostics = source.get("option_diagnostics")
        strict_eligible = 0
        if isinstance(diagnostics, dict):
            try:
                strict_eligible = int(diagnostics.get("strict_eligible_contracts", 0) or 0)
            except (TypeError, ValueError):
                strict_eligible = 0
        risk_decision = source.get("risk_decision")
        risk_approved = isinstance(risk_decision, dict) and risk_decision.get("decision") == (
            "APPROVE"
        )
        if item.option_assessment == "EXECUTION_ELIGIBLE" and not (
            strict_eligible > 0
            and isinstance(diagnostics, dict)
            and diagnostics.get("selected_structure_strictly_eligible") == 1
            and risk_approved
        ):
            raise ValueError(f"watchlist research overstated option eligibility for {item.symbol}")
    if result.as_of.tzinfo is None or result.as_of > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("watchlist research returned an unsupported freshness timestamp")
    return result
