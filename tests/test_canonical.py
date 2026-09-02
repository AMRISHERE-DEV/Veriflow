"""Canonical digest domain-separation and compatibility tests."""

from __future__ import annotations

import dataclasses
import unittest

from project_saturn._canonical import CanonicalizationError
from project_saturn.engine import ProposedClaim, assure, verify_receipt
from project_saturn.engine.contracts import canonical_hash, legacy_canonical_hash
from project_saturn.lanes.financial.models import stable_hash as financial_hash
from project_saturn.verify.status import VerifiabilityClass


class CanonicalHashTests(unittest.TestCase):
    def test_dictionary_order_is_irrelevant(self):
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))

    def test_list_and_tuple_are_domain_separated(self):
        self.assertNotEqual(canonical_hash({"v": [1, 2]}), canonical_hash({"v": (1, 2)}))
        self.assertNotEqual(financial_hash({"v": [1, 2]}), financial_hash({"v": (1, 2)}))

    def test_nonfinite_and_opaque_values_are_rejected(self):
        with self.assertRaises(CanonicalizationError):
            canonical_hash({"v": float("nan")})
        with self.assertRaises(CanonicalizationError):
            canonical_hash({"v": object()})

    def test_v1_receipt_remains_verifiable(self):
        proposed = ProposedClaim(
            "legacy", "unverified legacy claim", VerifiabilityClass.EMPIRICAL, "none")
        result = assure(proposed, sources={})
        content = {
            "engine_version": "project_saturn.engine.v1",
            "claim_id": result.receipt.claim_id,
            "raw_claim_sha256": result.receipt.raw_claim_sha256,
            "verification_plan": result.receipt.verification_plan,
            "cert_status": result.receipt.cert_status,
            "cert_source": result.receipt.cert_source,
            "cert_definitive_nonllm": result.receipt.cert_definitive_nonllm,
            "cert_record_hash": result.receipt.cert_record_hash,
            "advisories": [[name, severity] for name, severity in result.receipt.advisories],
            "release_decision": result.receipt.release_decision,
            "risk_tier": result.receipt.risk_tier,
            "disclaimer": result.receipt.disclaimer,
        }
        legacy = dataclasses.replace(
            result.receipt,
            engine_version="project_saturn.engine.v1",
            receipt_hash=legacy_canonical_hash(content),
            signature=None,
        )
        self.assertTrue(verify_receipt(legacy))
