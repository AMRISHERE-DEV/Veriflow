"""Class 7 pins: a ticker without a resolved CIK never binds an entity.

Found during kernel-benchmark design (2026-09-03): the financial lane only
compares CIKs when the claim carries one. A claim naming a TICKER but no CIK,
with a trusted text->concept binding proof, verified against ANOTHER filer's
trusted companyfacts whose same concept/period held the same value - and the
engine released it (VERIFIED / ALLOW / tier 0).

Policy (fail-closed): a ticker is an entity assertion; if it is present the
claim must also carry the resolved CIK, else the entity is unbound and the
claim stays UNVERIFIED. Claims carrying a CIK behave exactly as before.
"""
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from glass_ionomer.engine.pipeline import assure
from glass_ionomer.engine.reasoner import propose_financial
from glass_ionomer.lanes.financial import (
    FinancialClaim,
    issue_trusted_companyfacts,
    verify_financial_claim,
)
from glass_ionomer.verify.trust import issue_trusted_binding_proof

NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
REV = "RevenueFromContractWithCustomerExcludingAssessedTax"
VALUE = 383285000000.0


def _facts(cik: int):
    return issue_trusted_companyfacts({
        "cik": cik, "entityName": f"Filer {cik}",
        "facts": {"us-gaap": {REV: {"units": {"USD": [
            {"start": "2022-09-25", "end": "2023-09-30", "val": int(VALUE),
             "accn": f"{cik:010d}-23-000001", "fy": 2023, "fp": "FY", "form": "10-K"}]}}}}})


def _claim(*, cik=None, ticker=None):
    claim = FinancialClaim(claim_id="E1", text="Apple FY2023 revenue was 383285000000", concept=REV,
                           claimed_value=VALUE, unit="USD", cik=cik, ticker=ticker,
                           fiscal_year=2023, fiscal_period="FY")
    proof = issue_trusted_binding_proof("human", True, "test-stipulated", issuer="test",
                                        subject=claim.binding_subject())
    return replace(claim, binding_proof=proof)


class EntityBindingTests(unittest.TestCase):

    def test_ticker_without_cik_never_verifies_lane_level(self):
        pack = verify_financial_claim(_claim(ticker="AAPL"), _facts(999999), now=NOW)
        self.assertEqual(pack.status, "unverified")
        self.assertTrue(any("ticker" in r.lower() and "cik" in r.lower() for r in pack.reasons), pack.reasons)
        # even against the RIGHT filer, an unresolved ticker is unbound (fail closed)
        pack = verify_financial_claim(_claim(ticker="AAPL"), _facts(320193), now=NOW)
        self.assertEqual(pack.status, "unverified")

    def test_ticker_without_cik_never_releases_through_engine(self):
        c = _claim(ticker="AAPL")
        proposed = propose_financial(claim_id="E1", text=c.text, concept=REV, claimed_value=VALUE,
                                     fiscal_year=2023, ticker="AAPL", binding_proof=c.binding_proof)
        result = assure(proposed, sources={"companyfacts": _facts(999999)})
        self.assertNotEqual(getattr(result.certification.status, "value", result.certification.status), "verified")
        self.assertNotEqual(getattr(result.release_decision, "value", result.release_decision), "allow")

    def test_cik_binding_unchanged(self):
        self.assertEqual(verify_financial_claim(_claim(cik="320193", ticker="AAPL"), _facts(320193), now=NOW).status, "verified")
        self.assertEqual(verify_financial_claim(_claim(cik="320193", ticker="AAPL"), _facts(999999), now=NOW).status, "unverified")

    def test_entityless_tuple_claims_unchanged(self):
        # No ticker, no CIK: the caller supplies the authoritative record for the tuple
        # (existing contract, pinned so the fix stays minimal; wider rule is a ratification item).
        self.assertEqual(verify_financial_claim(_claim(), _facts(320193), now=NOW).status, "verified")


if __name__ == "__main__":
    unittest.main()
