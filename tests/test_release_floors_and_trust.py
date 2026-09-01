"""Release-layer floors and trust-proof serialization boundaries."""
from __future__ import annotations

import copy
import dataclasses
import json
import pickle
import unittest

from veriflow.engine.contracts import Certification, ProposedClaim, ReleaseDecision
from veriflow.engine.enforcer import enforce
from veriflow.verify.status import EvidenceStatus, VerifiabilityClass
from veriflow.verify.trust import (
    BindingProof,
    binding_proof_is_trusted,
    issue_trusted_binding_proof,
)


def _claim() -> ProposedClaim:
    return ProposedClaim(
        claim_id="c",
        text="two resolver-backed sources support the claim",
        verification_plan="none",
        payload={},
        verifiability_class=VerifiabilityClass.EMPIRICAL,
        advisories=(),
    )


class ModelDerivedSupportFloorTests(unittest.TestCase):
    def test_model_derived_only_corroboration_cannot_release(self):
        cert = Certification(
            status=EvidenceStatus.CORROBORATED,
            source="test",
            definitive_nonllm=False,
            model_derived_support_only=True,
        )
        decision, tier, _receipt, reasons = enforce(_claim(), cert)
        self.assertIs(decision, ReleaseDecision.REQUIRE_CLARIFICATION)
        self.assertGreaterEqual(tier, 3)
        self.assertTrue(any("model-derived" in r for r in reasons))

    def test_flag_absent_leaves_corroborated_release_unchanged(self):
        cert = Certification(
            status=EvidenceStatus.CORROBORATED,
            source="test",
            definitive_nonllm=False,
        )
        decision, _tier, _receipt, _reasons = enforce(_claim(), cert)
        self.assertIs(decision, ReleaseDecision.ALLOW_WITH_NOTICE)

    def test_floor_never_relaxes_a_stricter_decision(self):
        cert = Certification(
            status=EvidenceStatus.REFUTED,
            source="test",
            definitive_nonllm=True,
            model_derived_support_only=True,
        )
        decision, _tier, _receipt, _reasons = enforce(_claim(), cert)
        self.assertIs(decision, ReleaseDecision.REFUSE)


class TrustProofSerializationTests(unittest.TestCase):
    def _issued(self) -> tuple[BindingProof, dict]:
        subject = {"statement": "s", "spec": "spec-1"}
        return issue_trusted_binding_proof(
            "provenance", True, issuer="test", subject=subject), subject

    def test_issued_proof_is_trusted_for_its_subject(self):
        proof, subject = self._issued()
        self.assertTrue(binding_proof_is_trusted(proof, subject=subject))

    def test_pickle_round_trip_strips_trust(self):
        proof, subject = self._issued()
        clone = pickle.loads(pickle.dumps(proof))
        self.assertFalse(binding_proof_is_trusted(clone, subject=subject))

    def test_json_reconstruction_strips_trust(self):
        proof, subject = self._issued()
        data = json.loads(json.dumps(dataclasses.asdict(proof)))
        rebuilt = BindingProof(**data)
        self.assertFalse(binding_proof_is_trusted(rebuilt, subject=subject))

    def test_dataclasses_replace_strips_trust(self):
        proof, subject = self._issued()
        replaced = dataclasses.replace(proof)
        self.assertFalse(binding_proof_is_trusted(replaced, subject=subject))

    def test_deepcopy_strips_trust(self):
        proof, subject = self._issued()
        self.assertFalse(binding_proof_is_trusted(copy.deepcopy(proof), subject=subject))


if __name__ == "__main__":
    unittest.main()
