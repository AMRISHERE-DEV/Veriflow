"""Harness for the live-LLM Reasoner front end.

Uses an injected FAKE llm (no network) to prove the LLM is held to the advisory,
fail-open contract and cannot touch any invariant: it can structure and advise, but
it can never mint a status, never raise a release, and never produce a false VERIFIED.
"""
from __future__ import annotations

import json
import unittest

from glass_ionomer.engine import (
    ReleaseDecision,
    assure,
    assure_text,
    propose_financial,
    propose_with_llm,
    verify_receipt,
)
from glass_ionomer.lanes.financial import issue_trusted_companyfacts
from glass_ionomer.lanes.financial.models import financial_binding_subject
from glass_ionomer.verify.status import EvidenceStatus
from glass_ionomer.verify.trust import issue_trusted_binding_proof

CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"
FILED_VALUE = 383285000000.0


def companyfacts(value: float = FILED_VALUE) -> dict:
    # Stands in for an AUTHORITATIVE fetch -> trusted (a plain dict cannot mint VERIFIED/REFUTED).
    return issue_trusted_companyfacts({
        "cik": 320193,
        "facts": {"us-gaap": {CONCEPT: {"units": {"USD": [
            {"val": value, "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY",
             "form": "10-K", "start": "2022-09-25", "end": "2023-09-30", "frame": "CY2023"},
        ]}}}}
    })


def fake_json_llm(obj: dict):
    """An llm that returns a fixed JSON object regardless of the prompt."""
    return lambda prompt: json.dumps(obj)


def good_extraction(value: float = FILED_VALUE, confidence: float = 0.9) -> dict:
    return {
        "is_financial_fact": True, "concept": CONCEPT, "claimed_value": value,
        "unit": "USD", "fiscal_year": 2023, "fiscal_period": "FY",
        "cik": "0000320193", "ticker": "AAPL", "confidence": confidence, "notes": "ok",
    }


class TestLLMHappyPath(unittest.TestCase):
    def test_value_match_llm_binding_caps_below_verified(self):
        r = assure_text("Apple FY2023 revenue was $383.285 billion",
                        llm=fake_json_llm(good_extraction()),
                        sources={"companyfacts": companyfacts()})
        # The value matches the filing, but the text->concept binding is LLM-asserted (no trusted
        # proof), so the kernel caps the STATUS itself below VERIFIED - not merely the release.
        self.assertNotEqual(r.certification.status, EvidenceStatus.VERIFIED)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)
        names = [n for (n, _sev) in r.receipt.advisories]
        self.assertIn("llm_asserted_binding", names)
        self.assertTrue(verify_receipt(r.receipt))
        self.assertEqual(r.proposed.verification_plan, "financial.sec_xbrl")

    def test_deterministic_path_gets_clean_allow_for_same_value(self):
        # Contrast: a TRUSTED (non-LLM) text->concept proof + trusted source -> clean ALLOW.
        subject = financial_binding_subject(
            claim_id="d", text="rev", concept=CONCEPT, claimed_value=FILED_VALUE,
            fiscal_year=2023, fiscal_period="FY")
        det = propose_financial(claim_id="d", text="rev", concept=CONCEPT,
                                claimed_value=FILED_VALUE, fiscal_year=2023, fiscal_period="FY",
                                binding_proof=issue_trusted_binding_proof(
                                    "human", True, "test-stipulated text->concept",
                                    issuer="test", subject=subject))
        r = assure(det, sources={"companyfacts": companyfacts()})
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.ALLOW)


class TestLLMCannotFakeVerified(unittest.TestCase):
    def test_hallucinated_value_without_trusted_binding_is_not_definitively_refuted(self):
        r = assure_text("Apple FY2023 revenue was nine hundred ninety-nine billion",
                        llm=fake_json_llm(good_extraction(value=999_000_000_000.0)),
                        sources={"companyfacts": companyfacts(FILED_VALUE)})
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_max_confidence_cannot_force_clean_allow(self):
        r = assure_text("trust me", llm=fake_json_llm(good_extraction(confidence=1.0)),
                        sources={"companyfacts": companyfacts()})
        # Confidence 1.0 is INFO only; an LLM-asserted binding never reaches clean ALLOW.
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)

    def test_wrong_concept_extraction_cannot_definitively_refute_true_sentence(self):
        extraction = good_extraction()
        extraction["concept"] = "NetIncomeLoss"
        r = assure_text(
            "Apple FY2023 revenue was $383.285 billion",
            llm=fake_json_llm(extraction),
            sources={"companyfacts": companyfacts()},
        )
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertFalse(r.certification.definitive_nonllm)
        self.assertNotEqual(r.release_decision, ReleaseDecision.REFUSE)


class TestLLMFailOpen(unittest.TestCase):
    def test_garbage_reply_fails_open(self):
        r = assure_text("hello", llm=lambda p: "I'm not totally sure, but maybe ~$400B?",
                        sources={"companyfacts": companyfacts()})
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_llm_exception_fails_open(self):
        def boom(prompt):
            raise RuntimeError("api down")
        r = assure_text("anything", llm=boom, sources={"companyfacts": companyfacts()})
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)

    def test_non_financial_fails_open(self):
        obj = {"is_financial_fact": False, "concept": "", "claimed_value": None,
               "confidence": 0.2, "notes": "not a financial fact"}
        r = assure_text("The sky is blue.", llm=fake_json_llm(obj),
                        sources={"companyfacts": companyfacts()})
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)


class TestProposeWithLLMContract(unittest.TestCase):
    def test_returns_proposed_claim_only_no_status(self):
        p = propose_with_llm("Apple FY2023 revenue was $383.285B", llm=fake_json_llm(good_extraction()))
        self.assertEqual(p.verification_plan, "financial.sec_xbrl")
        self.assertEqual(p.payload["claimed_value"], FILED_VALUE)
        names = [a.name for a in p.advisories]
        self.assertIn("llm_asserted_binding", names)
        # A ProposedClaim has no status field at all - the Reasoner cannot certify.
        self.assertFalse(hasattr(p, "status"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
