"""Regression tests for the comparative-row period fallback (round-two gap 2).

Discovered by the 2026-09-03 blinded holdout (case H10): some filers' true
FY-N figure exists in companyfacts ONLY as comparative rows inside later
filings (labelled fy=N+1), so strict fy-label binding abstained on a true,
consistently-filed value.

Policy (fail-closed):
  * The strict fiscal-year LABEL doctrine remains the PRIMARY rule.
  * The fallback runs ONLY when zero rows carry the claimed fy/fp label.
  * Candidates are full-year duration rows whose represented period ends in
    the claimed fiscal year; all candidates across all accessions must agree
    exactly, else CONTESTED. Ambiguous period evidence stays UNVERIFIED.
  * The receipt must state:
    "Bound via comparative rows; no original-filing row present."
Inverse guard: wrong-period and wrong-value claims must refute exactly as
before (FIN-3 / H06 behavior preserved).
"""
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from veriflow.lanes.financial import (
    FinancialClaim,
    issue_trusted_companyfacts,
    verify_financial_claim,
)
from veriflow.verify.trust import issue_trusted_binding_proof

NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
NI = "NetIncomeLoss"
FALLBACK_RECEIPT = "Bound via comparative rows; no original-filing row present."


def _facts(rows):
    return issue_trusted_companyfacts({
        "cik": 1332551,
        "entityName": "Comparative-Only Test Corp.",
        "facts": {"us-gaap": {NI: {"units": {"USD": rows}}}},
    })


def _claim(value, fy=2024, fp="FY"):
    claim = FinancialClaim(
        claim_id="H10r", text=f"net income {value} for FY{fy}", concept=NI,
        claimed_value=value, fiscal_year=fy, fiscal_period=fp, unit="USD",
        cik="1332551",
    )
    proof = issue_trusted_binding_proof(
        "human", True, "test-stipulated text->concept", issuer="test",
        subject=claim.binding_subject(),
    )
    return replace(claim, binding_proof=proof)


# ACRES-shaped record: NO row is labelled fy=2024; the FY2024 figure exists only
# as comparative rows inside the FY2025 10-K and 10-K/A (both labelled fy=2025).
COMPARATIVE_ONLY = [
    {"start": "2025-01-01", "end": "2025-12-31", "val": 27976000,
     "accn": "0001332551-26-000010", "fy": 2025, "fp": "FY", "form": "10-K"},
    {"start": "2024-01-01", "end": "2024-12-31", "val": 28695000,
     "accn": "0001332551-26-000010", "fy": 2025, "fp": "FY", "form": "10-K"},
    {"start": "2025-01-01", "end": "2025-12-31", "val": 27977000,
     "accn": "0001332551-26-000020", "fy": 2025, "fp": "FY", "form": "10-K/A"},
    {"start": "2024-01-01", "end": "2024-12-31", "val": 28695000,
     "accn": "0001332551-26-000020", "fy": 2025, "fp": "FY", "form": "10-K/A"},
]


class ComparativeFallbackTests(unittest.TestCase):

    def test_fallback_verifies_consistent_comparative_rows(self):
        pack = verify_financial_claim(_claim(28695000), _facts(COMPARATIVE_ONLY), now=NOW)
        self.assertEqual(pack.status, "verified")
        self.assertIn(FALLBACK_RECEIPT, pack.reasons)
        # bound to the LATEST accession's comparative row
        self.assertEqual(pack.matched_fact.accession, "0001332551-26-000020")

    def test_fallback_wrong_value_is_refuted(self):
        pack = verify_financial_claim(_claim(99), _facts(COMPARATIVE_ONLY), now=NOW)
        self.assertEqual(pack.status, "refuted")
        self.assertIn(FALLBACK_RECEIPT, pack.reasons)

    def test_fallback_disagreement_is_contested(self):
        rows = [dict(r) for r in COMPARATIVE_ONLY]
        rows[3]["val"] = 28695001  # 10-K/A restates the FY2024 comparative
        pack = verify_financial_claim(_claim(28695000), _facts(rows), now=NOW)
        self.assertEqual(pack.status, "contested")

    def test_fallback_never_runs_when_label_exists(self):
        # Inverse guard: an original-filing row labelled fy=2024 is authoritative;
        # a claim matching only a DIFFERENT comparative value must not verify.
        rows = [dict(r) for r in COMPARATIVE_ONLY] + [
            {"start": "2024-01-01", "end": "2024-12-31", "val": 28000000,
             "accn": "0001332551-25-000005", "fy": 2024, "fp": "FY", "form": "10-K"},
        ]
        pack = verify_financial_claim(_claim(28695000), _facts(rows), now=NOW)
        self.assertNotEqual(pack.status, "verified")
        self.assertNotIn(FALLBACK_RECEIPT, pack.reasons)

    def test_wrong_period_claims_still_refute_or_abstain(self):
        # FIN-3 / H06 equivalent: labelled row exists for the claimed period,
        # claim carries another period's value -> refuted via the PRIMARY path.
        rows = [
            {"start": "2024-01-01", "end": "2024-12-31", "val": 500,
             "accn": "0001332551-25-000005", "fy": 2024, "fp": "FY", "form": "10-K"},
            {"start": "2023-01-01", "end": "2023-12-31", "val": 400,
             "accn": "0001332551-24-000005", "fy": 2023, "fp": "FY", "form": "10-K"},
        ]
        pack = verify_financial_claim(_claim(400, fy=2024), _facts(rows), now=NOW)
        self.assertEqual(pack.status, "refuted")
        self.assertNotIn(FALLBACK_RECEIPT, pack.reasons)
        # and a period with neither labels nor comparative rows stays unverified
        pack = verify_financial_claim(_claim(400, fy=2019), _facts(rows), now=NOW)
        self.assertEqual(pack.status, "unverified")

    def test_fallback_is_fy_only(self):
        pack = verify_financial_claim(_claim(28695000, fy=2024, fp="Q2"),
                                      _facts(COMPARATIVE_ONLY), now=NOW)
        self.assertEqual(pack.status, "unverified")

    def test_fallback_ambiguous_year_ends_fail_closed(self):
        # fiscal-year change: two full-year comparative rows END in 2024 with
        # different year-ends -> which one "FY2024" denotes is ambiguous.
        rows = [
            {"start": "2023-07-01", "end": "2024-06-30", "val": 111,
             "accn": "0001332551-26-000010", "fy": 2025, "fp": "FY", "form": "10-K"},
            {"start": "2024-01-01", "end": "2024-12-31", "val": 222,
             "accn": "0001332551-26-000020", "fy": 2025, "fp": "FY", "form": "10-K"},
        ]
        pack = verify_financial_claim(_claim(222), _facts(rows), now=NOW)
        self.assertEqual(pack.status, "unverified")

    def test_fallback_ignores_partial_year_durations(self):
        rows = [
            {"start": "2024-10-01", "end": "2024-12-31", "val": 7000,
             "accn": "0001332551-26-000010", "fy": 2025, "fp": "FY", "form": "10-K"},
        ]
        pack = verify_financial_claim(_claim(7000), _facts(rows), now=NOW)
        self.assertEqual(pack.status, "unverified")

    def test_fallback_still_requires_trusted_binding(self):
        claim = replace(_claim(28695000), binding_proof=None)
        pack = verify_financial_claim(claim, _facts(COMPARATIVE_ONLY), now=NOW)
        self.assertNotEqual(pack.status, "verified")


if __name__ == "__main__":
    unittest.main()
