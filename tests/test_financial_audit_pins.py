"""Regression pins extracted from the 2026-07 adversarial engine audit.

Only the pins whose imports live entirely in this public surface are carried
here; the audit's typed-adapter pins travel with those layers.
"""
from __future__ import annotations

import unittest
from dataclasses import replace

from project_saturn.lanes.financial import (
    FinancialClaim,
    issue_trusted_companyfacts,
    verify_financial_claim,
)
from project_saturn.verify.safe_arith import evaluate_relation
from project_saturn.verify.trust import issue_trusted_binding_proof


class FinancialAuditPins(unittest.TestCase):
    def _facts(self):
        return issue_trusted_companyfacts({"facts": {"us-gaap": {"Assets": {"units": {"USD": [
            {"val": 4_000_000_000_000, "accn": "a1", "fy": 2023, "fp": "FY",
             "form": "10-K", "end": "2023-12-31"},
        ]}}}}}, issuer="edgar")

    def _claim(self, value: float) -> FinancialClaim:
        claim = FinancialClaim(
            claim_id="c", text="assets", concept="Assets", claimed_value=value,
            unit="USD", fiscal_year=2023, fiscal_period="FY",
        )
        return replace(claim, binding_proof=issue_trusted_binding_proof(
            "provenance", True, issuer="test", subject=claim.binding_subject()))

    def test_fractional_claim_cannot_use_relative_window_to_verify(self):
        # Audit P0-1: a magnitude-scaled relative window once spanned whole
        # dollars at $4T; the value bar is a fixed absolute epsilon.
        result = verify_financial_claim(self._claim(4_000_000_000_003.4), self._facts())
        self.assertEqual(result.status, "refuted")

    def test_exact_match_with_trusted_binding_verifies(self):
        result = verify_financial_claim(self._claim(4_000_000_000_000), self._facts())
        self.assertEqual(result.status, "verified")

    def test_trillion_scale_equality_is_exact_in_safe_arith(self):
        # Sibling pin: the kernel comparator is exact; approximate equality
        # never belongs in the canonical mechanical-status gate.
        self.assertFalse(evaluate_relation("4000000000000.0 == 4000000000003.0"))
        self.assertTrue(evaluate_relation("4000000000000.0 < 4000000000003.0"))


if __name__ == "__main__":
    unittest.main()
