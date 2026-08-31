"""Harness for the propositional-logic lane (harvested from AmrThink, rewritten clean).

Proves the engine can now VERIFY and FALSIFY logical claims with a non-LLM deterministic
verifier - and that it stays honest: unchecked binding cannot mint VERIFIED, malformed/over-
large formulas fail open to UNVERIFIED, and advisory signals cannot raise a release.
"""
from __future__ import annotations

import unittest
from dataclasses import replace

from veriflow.engine import (
    AdvisorySignal,
    ReleaseDecision,
    Severity,
    assure,
    propose_logic,
    verify_receipt,
)
from veriflow.lanes.logic.verify import (
    LogicClaim,
    logic_binding_subject,
    verify_logic_claim,
)
from veriflow.verify.status import EvidenceStatus
from veriflow.verify.trust import issue_trusted_binding_proof

TRUSTED_BINDING = object()

P = ("var", "p")
Q = ("var", "q")
IMP_PQ = ("imp", P, Q)


def run(**kw):
    if kw.get("binding_proof") is TRUSTED_BINDING:
        subject = logic_binding_subject(
            claim_id=kw["claim_id"], text=kw["text"], mode=kw["mode"],
            constraints=tuple(kw.get("constraints", ())), target=kw.get("target"),
            model=kw.get("model"),
        )
        kw["binding_proof"] = issue_trusted_binding_proof(
            "human", True, "test-stipulated trusted binding", issuer="test", subject=subject)
    return assure(propose_logic(**kw), sources={})


def _bind(claim: LogicClaim) -> LogicClaim:
    return replace(claim, binding_proof=issue_trusted_binding_proof(
        "human", True, "test-stipulated trusted binding", issuer="test",
        subject=claim.binding_subject(),
    ))


class TestModelCheck(unittest.TestCase):
    def test_satisfying_model_verifies_and_releases(self):
        r = run(claim_id="m1", text="p->q and p, with p,q true", mode="model_check",
                constraints=(IMP_PQ, P), model={"p": True, "q": True}, binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.ALLOW)
        self.assertTrue(verify_receipt(r.receipt))

    def test_violating_model_is_refuted_with_counterexample(self):
        r = run(claim_id="m2", text="p->q and p, with p true q false", mode="model_check",
                constraints=(IMP_PQ, P), model={"p": True, "q": False}, binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.REFUTED)
        self.assertEqual(r.release_decision, ReleaseDecision.REFUSE)
        self.assertIn("counterexample", r.certification.detail)


class TestEntailment(unittest.TestCase):
    def test_modus_ponens_is_verified(self):
        r = run(claim_id="e1", text="(p->q), p  entails  q", mode="entailment",
                constraints=(IMP_PQ, P), target=Q, binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.ALLOW)

    def test_non_entailment_is_refuted(self):
        r = run(claim_id="e2", text="p does not entail q", mode="entailment",
                constraints=(P,), target=Q, binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.REFUTED)
        self.assertEqual(r.release_decision, ReleaseDecision.REFUSE)


class TestSatisfiability(unittest.TestCase):
    def test_contradiction_verified_as_unsatisfiable(self):
        r = run(claim_id="u1", text="p and not p is contradictory", mode="unsatisfiable",
                constraints=(P, ("not", P)), binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)

    def test_satisfiable_verified(self):
        r = run(claim_id="s1", text="p or q is satisfiable", mode="satisfiable",
                constraints=(("or", P, Q),), binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.VERIFIED)


class TestHonesty(unittest.TestCase):
    def test_unchecked_binding_cannot_verify(self):
        r = run(claim_id="b1", text="p->q, p, with p,q true", mode="model_check",
                constraints=(IMP_PQ, P), model={"p": True, "q": True}, binding_checked=False)
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertEqual(r.release_decision, ReleaseDecision.REQUIRE_CLARIFICATION)

    def test_bare_binding_bool_cannot_verify_unrelated_claim(self):
        # P0 regression: a bare binding_checked=True (no trusted proof) must NOT mint VERIFIED,
        # even when the formula is satisfiable and the claim text is unrelated to it.
        r = run(claim_id="p0", text="Company X will cure cancer by 2027 (unrelated to the formula)",
                mode="satisfiable", constraints=(("or", P, ("not", P)),), binding_checked=True)
        self.assertNotEqual(r.certification.status, EvidenceStatus.VERIFIED)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)

    def test_too_many_variables_abstains(self):
        many = tuple(("var", f"v{i}") for i in range(19))
        r = run(claim_id="big", text="huge", mode="entailment",
                constraints=many, target=("var", "v0"), binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)

    def test_enumeration_work_budget_abstains_before_exponential_search(self):
        many = tuple(("var", f"v{i}") for i in range(18))
        r = run(claim_id="expensive", text="large exact search", mode="unsatisfiable",
                constraints=many, binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertIn("work budget exceeded", r.certification.detail)

    def test_malformed_formula_fails_open(self):
        r = run(claim_id="bad", text="garbage op", mode="model_check",
                constraints=(("frobnicate", 1, 2),), model={"p": True}, binding_proof=TRUSTED_BINDING)
        self.assertEqual(r.certification.status, EvidenceStatus.UNVERIFIED)
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)

    def test_advisory_cannot_raise_unverifiable(self):
        agree = AdvisorySignal("models_agree", Severity.INFO, "all models say valid")
        r = run(claim_id="a1", text="p->q, p, p,q true", mode="model_check",
                constraints=(IMP_PQ, P), model={"p": True, "q": True},
                binding_checked=False, advisories=(agree,))
        self.assertNotEqual(r.release_decision, ReleaseDecision.ALLOW)


class TestDirect(unittest.TestCase):
    def test_counterexample_only_when_binding_checked(self):
        # binding unchecked -> no counterexample surfaced (the check is advisory)
        unchecked = verify_logic_claim(LogicClaim("d1", "x", "model_check",
                                                  constraints=(IMP_PQ,), model={"p": True, "q": False},
                                                  binding_checked=False))
        self.assertEqual(unchecked.status, EvidenceStatus.UNVERIFIED)
        self.assertIsNone(unchecked.counterexample)
        checked = verify_logic_claim(_bind(LogicClaim(
            "d2", "x", "model_check", constraints=(IMP_PQ,),
            model={"p": True, "q": False})))
        self.assertEqual(checked.status, EvidenceStatus.REFUTED)
        self.assertIsNotNone(checked.counterexample)

    def test_binding_proof_cannot_be_replayed_onto_another_logic_claim(self):
        original = _bind(LogicClaim("original", "p is satisfiable", "satisfiable", constraints=(P,)))
        replayed = LogicClaim(
            "replayed", "q is satisfiable", "satisfiable", constraints=(Q,),
            binding_proof=original.binding_proof,
        )
        self.assertEqual(verify_logic_claim(original).status, EvidenceStatus.VERIFIED)
        self.assertEqual(verify_logic_claim(replayed).status, EvidenceStatus.UNVERIFIED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
