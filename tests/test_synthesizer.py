"""Harness for the Synthesizer organ - compose certified claims into a conclusion.

Proves the engine does MORE than a per-claim badge (it builds defensible conclusions with
a provenance graph) while never laundering: weakest-link, ceiling-capped, unchecked-
derivation escalates, refuted premise breaks the conjunction, and the graph anchor is
tamper-evident.
"""
from __future__ import annotations

import dataclasses
import unittest

from glass_ionomer.engine import (
    ProposedClaim,
    ReleaseDecision,
    assure,
    derivation_subject,
    issue_trusted_derivation_proof,
    propose_financial,
    synthesize,
    verify_conclusion,
)
from glass_ionomer.lanes.financial import issue_trusted_companyfacts
from glass_ionomer.lanes.financial.models import financial_binding_subject
from glass_ionomer.verify.status import VerifiabilityClass
from glass_ionomer.verify.trust import issue_trusted_binding_proof

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"
NI = "NetIncomeLoss"
REV_VAL, NI_VAL = 383285000000.0, 96995000000.0


def companyfacts() -> dict:
    def row(v):
        return [{"val": v, "accn": "0000320193-23-000106", "fy": 2023, "fp": "FY",
                 "form": "10-K", "start": "2022-09-25", "end": "2023-09-30", "frame": "CY2023"}]
    return issue_trusted_companyfacts({"facts": {"us-gaap": {
        REV: {"units": {"USD": row(REV_VAL)}},
        NI: {"units": {"USD": row(NI_VAL)}},
    }}})


def premise(concept, value, cid):
    text = f"{concept}={value}"
    subject = financial_binding_subject(
        claim_id=cid, text=text, concept=concept, claimed_value=value,
        fiscal_year=2023, fiscal_period="FY")
    return assure(propose_financial(claim_id=cid, text=f"{concept}={value}", concept=concept,
                                    claimed_value=value, fiscal_year=2023, fiscal_period="FY",
                                    binding_proof=issue_trusted_binding_proof(
                                        "human", True, "test-stipulated text->concept",
                                        issuer="test", subject=subject)),
                  sources={"companyfacts": companyfacts()})


def conclusion_claim(vclass=VerifiabilityClass.MECHANICAL, cid="concl"):
    return ProposedClaim(claim_id=cid, text="derived conclusion",
                         verifiability_class=vclass, verification_plan="synthesis")


def verified_premises():
    return [premise(REV, REV_VAL, "p_rev"), premise(NI, NI_VAL, "p_ni")]


class TestConjunctionAndDerivation(unittest.TestCase):
    def test_bare_checked_derivation_flag_still_escalates(self):
        r = synthesize(conclusion_claim(), verified_premises(), derivation_checked=True)
        self.assertEqual(r.composed_status, "verified")
        self.assertEqual(r.release_decision, ReleaseDecision.ESCALATE)
        self.assertEqual(len(r.support_graph), 2)
        self.assertIn("derivation_proof_required", [name for name, _ in r.receipt.advisories])
        self.assertTrue(verify_conclusion(r))

    def test_unchecked_derivation_escalates(self):
        r = synthesize(conclusion_claim(), verified_premises(), derivation_checked=False)
        # The premises are VERIFIED, but the inference linking them is not checked ...
        self.assertEqual(r.composed_status, "verified")
        # ... so the conclusion does NOT release - defense-in-depth escalates.
        self.assertEqual(r.release_decision, ReleaseDecision.ESCALATE)
        names = [n for (n, _s) in r.receipt.advisories]
        self.assertIn("derivation_binding_unchecked", names)

    def test_exact_trusted_derivation_releases_verified_conclusion(self):
        conclusion = conclusion_claim()
        premises = verified_premises()
        proof = issue_trusted_derivation_proof(
            subject=derivation_subject(conclusion, premises), issuer="test")
        r = synthesize(conclusion, premises, derivation_proof=proof)
        self.assertEqual(r.composed_status, "verified")
        self.assertEqual(r.release_decision, ReleaseDecision.ALLOW)

    def test_derivation_proof_cannot_be_replayed_onto_another_conclusion(self):
        original = conclusion_claim(cid="original")
        premises = verified_premises()
        proof = issue_trusted_derivation_proof(
            subject=derivation_subject(original, premises), issuer="test")
        replayed = conclusion_claim(cid="replayed")
        r = synthesize(replayed, premises, derivation_proof=proof)
        self.assertEqual(r.composed_status, "verified")
        self.assertEqual(r.release_decision, ReleaseDecision.ESCALATE)
        self.assertIn("derivation_proof_invalid", [name for name, _ in r.receipt.advisories])


class TestWeakestLink(unittest.TestCase):
    def test_unverified_premise_caps_conclusion(self):
        prem = [premise(REV, REV_VAL, "p_rev"),
                assure(propose_financial(claim_id="p_missing", text="x", concept="NoSuchConcept",
                                         claimed_value=1.0, fiscal_year=2023),
                       sources={"companyfacts": companyfacts()})]
        r = synthesize(conclusion_claim(), prem, derivation_checked=True)
        self.assertEqual(r.composed_status, "unverified")
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_refuted_premise_breaks_conjunction(self):
        prem = [premise(REV, REV_VAL, "p_rev"),
                premise(NI, 1.0, "p_wrong")]  # wrong NI value -> REFUTED
        r = synthesize(conclusion_claim(), prem, derivation_checked=True)
        self.assertEqual(r.composed_status, "refuted")
        self.assertEqual(r.release_decision, ReleaseDecision.REFUSE)


class TestCeilingCap(unittest.TestCase):
    def test_interpretive_conclusion_capped_below_verified(self):
        # Two VERIFIED mechanical premises, but the conclusion is INTERPRETIVE ->
        # it is capped at its own ceiling (SUPPORTED), never inherits VERIFIED.
        r = synthesize(conclusion_claim(VerifiabilityClass.INTERPRETIVE),
                       verified_premises(), derivation_checked=True)
        self.assertEqual(r.composed_status, "supported")
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)


class TestNoPremises(unittest.TestCase):
    def test_empty_is_unverified(self):
        r = synthesize(conclusion_claim(), [], derivation_checked=True)
        self.assertEqual(r.composed_status, "unverified")
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)


class TestNoLaundering(unittest.TestCase):
    def test_graph_hash_is_order_independent(self):
        a = synthesize(conclusion_claim(), [premise(REV, REV_VAL, "p_rev"), premise(NI, NI_VAL, "p_ni")],
                       derivation_checked=True)
        b = synthesize(conclusion_claim(), [premise(NI, NI_VAL, "p_ni"), premise(REV, REV_VAL, "p_rev")],
                       derivation_checked=True)
        self.assertEqual(a.graph_hash, b.graph_hash)

    def test_tampering_a_premise_node_is_detected(self):
        r = synthesize(conclusion_claim(), verified_premises(), derivation_checked=True)
        self.assertTrue(verify_conclusion(r))
        bad_node = dataclasses.replace(r.support_graph[0], record_hash="forged")
        tampered = dataclasses.replace(r, support_graph=(bad_node, *r.support_graph[1:]))
        self.assertFalse(verify_conclusion(tampered))

    def test_tampering_definitive_bit_or_empty_graph_is_detected(self):
        r = synthesize(conclusion_claim(), verified_premises(), derivation_checked=True)
        changed = dataclasses.replace(
            r.support_graph[0], definitive_nonllm=not r.support_graph[0].definitive_nonllm)
        self.assertFalse(verify_conclusion(dataclasses.replace(
            r, support_graph=(changed, *r.support_graph[1:]))))

        empty = synthesize(conclusion_claim(), [])
        self.assertTrue(verify_conclusion(empty))
        self.assertFalse(verify_conclusion(dataclasses.replace(empty, graph_hash="")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
