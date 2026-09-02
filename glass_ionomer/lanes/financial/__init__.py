"""Financial (SEC/XBRL) verification lane.

Verify a structured financial claim against an authoritative EDGAR companyfacts
record, routed through the hardened glass_ionomer.verify VERIFIED gate. VERIFIED means
"matches the figure as reported in the cited filing (accession)", never economic truth.
"""
from .extractor import extract_facts, select_period
from .models import (
    AssuranceTier,
    EvidencePack,
    FinancialClaim,
    SecFact,
    TrustedCompanyfacts,
    companyfacts_are_trusted,
    issue_trusted_companyfacts,
    parse_money,
    stable_hash,
)
from .verify import CANDIDATE_METHOD, verify_financial_claim, verify_prose_claim

__all__ = [
    "CANDIDATE_METHOD",
    "AssuranceTier",
    "EvidencePack",
    "FinancialClaim",
    "SecFact",
    "TrustedCompanyfacts",
    "companyfacts_are_trusted",
    "extract_facts",
    "issue_trusted_companyfacts",
    "parse_money",
    "select_period",
    "stable_hash",
    "verify_financial_claim",
    "verify_prose_claim",
]
