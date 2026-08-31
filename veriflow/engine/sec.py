"""SEC/XBRL convenience entrypoint for the unified engine.

This module is deliberately thin: it resolves/fetches authoritative SEC
companyfacts when needed, then delegates to the existing
Reasoner -> Verifier -> Enforcer path. It defines no status logic of its own.
"""
from __future__ import annotations

from collections.abc import Callable

from veriflow.lanes.financial.models import stable_hash
from veriflow.lanes.financial.resolver import fetch_companyfacts, ticker_to_cik

from .contracts import EngineResult
from .pipeline import assure
from .reasoner import propose_financial

TickerResolver = Callable[..., str | None]
CompanyfactsFetcher = Callable[..., dict | None]


def _default_claim_id(
    *,
    text: str,
    concept: str,
    claimed_value: float,
    fiscal_year: int | None,
    fiscal_period: str,
    unit: str,
    cik: str | None,
    ticker: str | None,
) -> str:
    return "sec:" + stable_hash({
        "text": text,
        "concept": concept,
        "claimed_value": claimed_value,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "unit": unit,
        "cik": cik,
        "ticker": ticker,
    })[:16]


def _resolve_ticker(
    ticker: str | None,
    *,
    user_agent: str | None,
    timeout: float,
    ticker_resolver: TickerResolver,
) -> str | None:
    if not ticker:
        return None
    try:
        return ticker_resolver(ticker, user_agent=user_agent, timeout=timeout)
    except Exception:
        return None


def _fetch_facts(
    cik: str | None,
    *,
    user_agent: str | None,
    timeout: float,
    fetcher: CompanyfactsFetcher,
) -> dict | None:
    if not cik:
        return None
    try:
        return fetcher(cik, user_agent=user_agent, timeout=timeout)
    except Exception:
        return None


def assure_sec_claim(
    *,
    text: str,
    concept: str,
    claimed_value: float,
    fiscal_year: int | None,
    fiscal_period: str = "FY",
    unit: str = "USD",
    ticker: str | None = None,
    cik: str | None = None,
    form: str | None = None,
    binding_proof=None,                      # a TRUSTED text->concept proof is required for VERIFIED
    claim_id: str | None = None,
    companyfacts: dict | None = None,
    user_agent: str | None = None,
    timeout: float = 20.0,
    signing_key: str | bytes | None = None,
    ticker_resolver: TickerResolver = ticker_to_cik,
    fetcher: CompanyfactsFetcher = fetch_companyfacts,
) -> EngineResult:
    """Assure a structured SEC financial claim and return the engine Evidence Card.

    `companyfacts` may be supplied directly for offline/replayable use. If absent,
    the helper resolves `ticker` to CIK when needed, fetches SEC companyfacts via
    the injected fetcher, then passes that independent source into the existing
    engine. Resolver/fetch failures are intentionally swallowed: the downstream
    verifier sees no authoritative source and returns UNVERIFIED.
    """
    resolved_cik = cik
    if companyfacts is None and not resolved_cik:
        resolved_cik = _resolve_ticker(
            ticker,
            user_agent=user_agent,
            timeout=timeout,
            ticker_resolver=ticker_resolver,
        )

    fetched = companyfacts
    if fetched is None and resolved_cik:
        fetched = _fetch_facts(
            resolved_cik,
            user_agent=user_agent,
            timeout=timeout,
            fetcher=fetcher,
        )

    cid = claim_id or _default_claim_id(
        text=text,
        concept=concept,
        claimed_value=claimed_value,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        unit=unit,
        cik=resolved_cik,
        ticker=ticker,
    )
    proposed = propose_financial(
        claim_id=cid,
        text=text,
        concept=concept,
        claimed_value=claimed_value,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        unit=unit,
        cik=resolved_cik,
        ticker=ticker,
        form=form,
        binding_proof=binding_proof,
    )
    sources = {"companyfacts": fetched} if fetched else {}
    return assure(proposed, sources=sources, signing_key=signing_key)
