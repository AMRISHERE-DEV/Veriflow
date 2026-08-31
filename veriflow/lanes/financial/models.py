"""Typed contracts for the financial (SEC/XBRL) verification lane.

A FinancialClaim is a STRUCTURED claim: "entity X reported GAAP concept C = value
V for period P, as filed." The lane verifies it against an authoritative EDGAR
companyfacts record and emits an EvidencePack.

Honesty by construction: VERIFIED means only "this value matches the figure the
filing actually reports (accession is the proof)." It does NOT assert economic
truth. That distinction is the moat.

Stdlib only.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from enum import Enum

from veriflow._canonical import CanonicalizationError, canonical_sha256
from veriflow.verify.trust import _STAMP, BindingProof

_COMPANYFACTS_TRUST_TOKEN = object()
_COMPANYFACTS_CANONICAL_NODE_LIMIT = 500_000


class TrustedCompanyfacts(dict):
    """Dict wrapper for a companyfacts record fetched from an AUTHORITATIVE source (EDGAR) or an
    audited fixture. The trust token is stamped ONLY via `issue_trusted_companyfacts` (which holds
    the private `_STAMP`); a plain dict, a direct construction, or a subclass is NOT authoritative,
    so a forged companyfacts payload can never mint VERIFIED."""

    issuer: str
    _trust_token: object | None
    _trusted_content_hash: str

    def __init__(self, facts=None, *, issuer: str = "system", _stamp=None):
        super().__init__(facts or {})
        self.issuer = issuer
        self._trust_token = _COMPANYFACTS_TRUST_TOKEN if _stamp is _STAMP else None


def issue_trusted_companyfacts(facts: dict, *, issuer: str = "system") -> TrustedCompanyfacts:
    """Sole issuance point for authoritative companyfacts (the trusted EDGAR fetcher + audited fixtures)."""
    trusted = TrustedCompanyfacts(copy.deepcopy(facts), issuer=issuer, _stamp=_STAMP)
    trusted._trusted_content_hash = _companyfacts_hash(dict(trusted))
    return trusted


def companyfacts_are_trusted(facts) -> bool:
    try:
        return bool(
            isinstance(facts, TrustedCompanyfacts)
            and getattr(facts, "_trust_token", None) is _COMPANYFACTS_TRUST_TOKEN
            and getattr(facts, "_trusted_content_hash", None) == _companyfacts_hash(dict(facts))
        )
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        return False


class AssuranceTier(Enum):
    L0 = "L0_model_hypothesis"     # unanchored / no authoritative binding
    L1 = "L1_quote_located"       # quote located; interpretation model-attributed (Lane B)
    L2 = "L2_quote_coverage"      # quote located + kernel candidate coverage (Lane B+)
    L3 = "L3_deterministic"       # deterministic authoritative match (Lane A) -> VERIFIED-capable


def stable_hash(obj) -> str:
    """Strict type-preserving digest used by financial manifests and snapshots."""
    return canonical_sha256(obj)


def _companyfacts_hash(obj) -> str:
    """Hash a byte-capped EDGAR document without relaxing global trust limits."""
    return canonical_sha256(obj, max_nodes=_COMPANYFACTS_CANONICAL_NODE_LIMIT)


_MONEY = re.compile(
    r"(?i)(?P<sign>-)?(?P<currency>\$)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>trillion|billion|million|thousand|bn|mm|tn|[bmkt])?\b"
)
_BARE_MONEY = re.compile(r"^\s*(?P<sign>-)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*$")
_SCALES = {
    "": 1.0, "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "tn": 1e12, "trillion": 1e12,
}


def parse_money(text: str) -> float | None:
    """Deterministically parse '$383.3B' / '383.3 billion' / '383285000000' -> base float.
    Pure code: the magnitude word is the only interpretation and it is a FIXED table, so
    this never launders. In prose, an amount must carry ``$`` or a magnitude word;
    otherwise only a string containing one bare number is accepted. Ambiguous prose
    returns None instead of binding a year or percentage as money."""
    matches = list(_MONEY.finditer(text or ""))
    marked = [m for m in matches if m.group("currency") or m.group("scale")]
    if len(marked) > 1:
        return None
    match = marked[0] if marked else _BARE_MONEY.fullmatch(text or "")
    if match is None:
        return None
    num = float(match.group("num").replace(",", ""))
    scale = _SCALES.get((match.groupdict().get("scale") or "").lower(), 1.0)
    val = num * scale
    return -val if match.group("sign") else val


@dataclass(frozen=True)
class FinancialClaim:
    claim_id: str
    text: str
    concept: str                 # US-GAAP tag, e.g. RevenueFromContractWithCustomerExcludingAssessedTax
    claimed_value: float         # base units (e.g. USD)
    unit: str = "USD"
    cik: str | None = None
    ticker: str | None = None
    fiscal_year: int | None = None   # REQUIRED for a positive status (no wildcard)
    fiscal_period: str = "FY"           # FY, Q1, Q2, Q3, Q4
    form: str | None = None          # advisory only; never used to prune a restatement
    # The text->concept binding (does this claim's prose actually refer to `concept`?) is NOT checkable
    # in code. Only a TRUSTED BindingProof (factory-issued) lets the claim reach VERIFIED; without it the
    # tuple may still match a filing but the CLAIM caps below VERIFIED (avoids the "net income"=GrossProfit overclaim).
    binding_proof: BindingProof | None = None
    # NOTE: there is intentionally no caller tolerance knob. The value bar is a fixed
    # half-cent absolute tolerance in the verifier -> the claimant cannot widen the match.

    def binding_subject(self) -> dict:
        return financial_binding_subject(
            claim_id=self.claim_id,
            text=self.text,
            concept=self.concept,
            claimed_value=self.claimed_value,
            unit=self.unit,
            cik=self.cik,
            ticker=self.ticker,
            fiscal_year=self.fiscal_year,
            fiscal_period=self.fiscal_period,
            form=self.form,
        )


def financial_binding_subject(
    *, claim_id: str, text: str, concept: str, claimed_value: float,
    unit: str = "USD", cik=None, ticker=None, fiscal_year=None,
    fiscal_period: str = "FY", form=None,
) -> dict:
    """Canonical prose-to-XBRL tuple reviewed by a financial binding proof."""
    return {
        "lane": "financial.sec_xbrl",
        "claim_id": claim_id,
        "text": text,
        "concept": concept,
        "claimed_value": claimed_value,
        "unit": unit,
        "cik": cik,
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "form": form,
    }


@dataclass(frozen=True)
class SecFact:
    concept: str
    value: float
    unit: str
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    start: str | None
    end: str | None
    frame: str | None
    accession: str               # provenance anchor: WHICH filing reported this value


@dataclass(frozen=True)
class EvidencePack:
    """The lane's deliverable. The PACK is the product; the status label is secondary."""
    claim_id: str
    status: str                       # EvidenceStatus value (from the kernel)
    tier: AssuranceTier
    lane: str                         # "A" | "B" | "none"
    matched_fact: SecFact | None
    as_reported_in: str | None     # accession; VERIFIED == "matches the value as reported in this filing"
    reasons: tuple
    manifest_hash: str
    kernel_wording: str               # honest, tier-capped sentence (the only language consumers may show)
