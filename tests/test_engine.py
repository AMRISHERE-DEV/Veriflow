"""Harness for the unified engine - AmrThink-style swap-ablation + invariant property tests.

The tests are written to FAIL if the synthesis ever regresses to the failure mode that
plagued every prior artifact: a release justified by anything other than a non-LLM
verifier matching an authoritative record. Each invariant from contracts.py has a test.
"""
from __future__ import annotations

import dataclasses
import unittest

from veriflow.engine import (
    DISCLAIMER,
    AdvisorySignal,
    ReleaseDecision,
    Severity,
    assure,
    certify,
    propose_financial,
    propose_unstructured,
    verify_receipt,
)
from veriflow.engine.contracts import Certification, ProposedClaim
from veriflow.engine.enforcer import enforce
from veriflow.lanes.financial import issue_trusted_companyfacts
from veriflow.lanes.financial.models import financial_binding_subject
from veriflow.verify.status import EvidenceStatus
from veriflow.verify.trust import issue_trusted_binding_proof

CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"
FILED_VALUE = 383285000000.0


def companyfacts(value: float = FILED_VALUE) -> dict:
    """One authoritative filed figure for FY2023. `value` is the record we can SWAP.
    Trusted (authoritative-source stand-in); a plain dict cannot mint VERIFIED/REFUTED."""
    return issue_trusted_companyfacts({
        "cik": 320193,
        "facts": {"us-gaap": {CONCEPT: {"units": {"USD": [
            {"val": value, "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY",
             "form": "10-K", "start": "2022-09-25", "end": "2023-09-30", "frame": "CY2023"},
        ]}}}}
    })


def claim(value: float = FILED_VALUE, *, advisories=()) -> ProposedClaim:
    subject = financial_binding_subject(
        claim_id="c1", text=f"Apple FY2023 revenue was {value}", concept=CONCEPT,
        claimed_value=value, fiscal_year=2023, fiscal_period="FY",
        cik="0000320193", ticker="AAPL",
    )
    return propose_financial(
        claim_id="c1",
        text=f"Apple FY2023 revenue was {value}",
        concept=CONCEPT, claimed_value=value,
        fiscal_year=2023, fiscal_period="FY", cik="0000320193", ticker="AAPL",
        binding_proof=issue_trusted_binding_proof(
            "human", True, "test-stipulated text->concept", issuer="test", subject=subject),
        advisories=advisories,
    )


class TestReleaseOnVerified(unittest.TestCase):
    def test_exact_match_releases(self):
        r = assure(claim(FILED_VALUE), sources={"companyfacts": companyfacts()})
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)
        self.assertTrue(r.certification.definitive_nonllm)
        self.assertEqual(r.release_decision, ReleaseDecision.ALLOW)
        self.assertEqual(r.risk_tier, 0)
        self.assertTrue(verify_receipt(r.receipt))

    def test_contested_financial_pack_is_not_marked_definitive(self):
        facts = issue_trusted_companyfacts({
            "cik": 320193,
            "facts": {"us-gaap": {CONCEPT: {"units": {"USD": [
                {"val": FILED_VALUE, "accn": "a", "fy": 2023, "fp": "FY", "form": "10-K"},
                {"val": FILED_VALUE + 1, "accn": "b", "fy": 2023, "fp": "FY", "form": "10-K/A"},
            ]}}}},
        })
        certification = certify(claim(FILED_VALUE), sources={"companyfacts": facts})
        self.assertEqual(certification.status, EvidenceStatus.CONTESTED)
        self.assertFalse(certification.definitive_nonllm)


class TestSwapAblation(unittest.TestCase):
    """Swap the AUTHORITATIVE RECORD (claim unchanged) -> the decision must flip.
    Proves the release tracks the non-LLM record, not the claim or any LLM signal."""

    def test_record_swap_flips_release(self):
        same_claim = claim(FILED_VALUE)
        matching = assure(same_claim, sources={"companyfacts": companyfacts(FILED_VALUE)})
        swapped = assure(same_claim, sources={"companyfacts": companyfacts(999_000_000_000.0)})

        self.assertEqual(matching.release_decision, ReleaseDecision.ALLOW)
        self.assertEqual(matching.certification.status, EvidenceStatus.VERIFIED)

        self.assertEqual(swapped.certification.status, EvidenceStatus.REFUTED)
        self.assertEqual(swapped.release_decision, ReleaseDecision.REFUSE)
        # The ONLY thing that changed was the authoritative record.
        self.assertNotEqual(matching.release_decision, swapped.release_decision)


class TestModelAgreementIsNotEvidence(unittest.TestCase):
    """Maximal LLM 'agreement' can never mint a release without the non-LLM verifier."""

    def test_agreement_cannot_release_unverifiable(self):
        # Concept absent from the record -> non-LLM verifier returns UNVERIFIED.
        empty = {"facts": {"us-gaap": {}}}
        agree = AdvisorySignal("all_models_agree_true", Severity.INFO,
                               "5/5 frontier models rated this TRUE with 0.99 confidence")
        r = assure(claim(FILED_VALUE, advisories=(agree,)), sources={"companyfacts": empty})
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_agreement_cannot_rescue_refuted(self):
        agree = AdvisorySignal("all_models_agree_true", Severity.INFO, "models insist it is true")
        # Claim value does NOT match the filed record -> REFUTED, agreement notwithstanding.
        r = assure(claim(999_000_000_000.0, advisories=(agree,)),
                   sources={"companyfacts": companyfacts(FILED_VALUE)})
        self.assertEqual(r.certification.status, EvidenceStatus.REFUTED)
        self.assertEqual(r.release_decision, ReleaseDecision.REFUSE)


class TestAdvisoriesOnlyLower(unittest.TestCase):
    """Invariant (2): an advisory may lower a release, never raise it."""

    def test_block_downgrades_verified_to_refuse(self):
        block = AdvisorySignal("prompt_injection_detected", Severity.BLOCK, "tool output tried to override")
        r = assure(claim(FILED_VALUE, advisories=(block,)), sources={"companyfacts": companyfacts()})
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)  # core still verified
        self.assertEqual(r.release_decision, ReleaseDecision.REFUSE)       # but not released
        self.assertEqual(r.risk_tier, 5)

    def test_caution_downgrades_allow_to_notice(self):
        caution = AdvisorySignal("high_stakes_context", Severity.CAUTION, "medical/financial advice")
        r = assure(claim(FILED_VALUE, advisories=(caution,)), sources={"companyfacts": companyfacts()})
        self.assertEqual(r.release_decision, ReleaseDecision.ALLOW_WITH_NOTICE)

    def test_info_does_not_change_decision(self):
        info = AdvisorySignal("note", Severity.INFO, "fyi")
        r = assure(claim(FILED_VALUE, advisories=(info,)), sources={"companyfacts": companyfacts()})
        self.assertEqual(r.release_decision, ReleaseDecision.ALLOW)


class TestDefenseInDepth(unittest.TestCase):
    """Even a VERIFIED status without a non-LLM definitive verifier must NOT release."""

    def test_verified_without_definitive_escalates(self):
        forged = Certification(status=EvidenceStatus.VERIFIED, source="llm_claimed",
                               definitive_nonllm=False, record_hash="x")
        proposed = propose_unstructured(claim_id="c", text="t")
        decision, tier, receipt, reasons = enforce(proposed, forged)
        self.assertEqual(decision, ReleaseDecision.ESCALATE)
        self.assertNotEqual(decision, ReleaseDecision.ALLOW)


class TestFailModes(unittest.TestCase):
    def test_fail_open_unstructured(self):
        r = assure(propose_unstructured(claim_id="c", text="vibes only"), sources={})
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_fail_closed_missing_source(self):
        r = assure(claim(FILED_VALUE), sources={})  # financial plan but no companyfacts
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)

    def test_period_underspecified_never_releases(self):
        p = propose_financial(claim_id="c", text="rev", concept=CONCEPT,
                              claimed_value=FILED_VALUE, fiscal_year=None)
        r = assure(p, sources={"companyfacts": companyfacts()})
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)


class TestReceiptReplay(unittest.TestCase):
    def test_determinism_same_inputs_same_hash(self):
        a = assure(claim(FILED_VALUE), sources={"companyfacts": companyfacts()})
        b = assure(claim(FILED_VALUE), sources={"companyfacts": companyfacts()})
        self.assertEqual(a.receipt.receipt_hash, b.receipt.receipt_hash)

    def test_tamper_is_detected(self):
        r = assure(claim(FILED_VALUE), sources={"companyfacts": companyfacts()})
        self.assertTrue(verify_receipt(r.receipt))
        tampered = dataclasses.replace(r.receipt, release_decision="refuse")
        self.assertFalse(verify_receipt(tampered))

    def test_signed_receipt_roundtrip(self):
        r = assure(claim(FILED_VALUE), sources={"companyfacts": companyfacts()}, signing_key="secret")
        self.assertIsNotNone(r.receipt.signature)
        self.assertTrue(verify_receipt(r.receipt, signing_key="secret"))
        self.assertFalse(verify_receipt(r.receipt, signing_key="wrong-key"))

    def test_empty_signing_key_is_rejected_and_malformed_receipt_fails_closed(self):
        with self.assertRaises(ValueError):
            assure(claim(FILED_VALUE), sources={"companyfacts": companyfacts()}, signing_key="")
        r = assure(claim(FILED_VALUE), sources={"companyfacts": companyfacts()})
        malformed = dataclasses.replace(r.receipt, advisories=(("missing-severity",),))
        self.assertFalse(verify_receipt(malformed))


class TestAntiOverclaim(unittest.TestCase):
    def test_receipt_carries_record_not_judgment_disclaimer(self):
        r = assure(claim(FILED_VALUE), sources={"companyfacts": companyfacts()})
        self.assertEqual(r.receipt.disclaimer, DISCLAIMER)
        self.assertIn("not integrity-of-judgment", r.receipt.disclaimer)
        self.assertIn("Model agreement is never evidence", r.receipt.disclaimer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
