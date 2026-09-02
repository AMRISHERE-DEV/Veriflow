from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from glass_ionomer.verify import (
    AuthorityTier,
    Entailment,
    EvidenceRecord,
    EvidenceStatus,
    Lead,
    ResolvedSource,
    chain_resolvers,
    make_inmemory_resolver,
    resolve,
    verify_text,
)
from glass_ionomer.verify.resolver import _crude_text

NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)


class ProductionEvidenceBoundaryTests(unittest.TestCase):
    def _raw(self, lineage: str) -> EvidenceRecord:
        return EvidenceRecord.of(
            lineage,
            f"invented support from {lineage}",
            published=NOW - timedelta(days=1),
            stance=Entailment.ENTAILS,
        )

    def test_self_hashed_caller_records_cannot_corroborate(self):
        out = verify_text(
            "Vitamin D reduces fracture risk in adults.",
            [self._raw("fake-a"), self._raw("fake-b")],
            (),
            now=NOW,
        )
        self.assertEqual(out.decision.status, EvidenceStatus.SUPPORTED)
        self.assertFalse(out.decision.provenance_verified_support)

    def test_production_mode_rejects_raw_records_as_positive_evidence(self):
        out = verify_text(
            "Vitamin D reduces fracture risk in adults.",
            [self._raw("fake-a"), self._raw("fake-b")],
            (),
            now=NOW,
            require_provenance=True,
        )
        self.assertEqual(out.decision.status, EvidenceStatus.UNVERIFIED)
        self.assertTrue(all(not item.applicable for item in out.evidence_items))

    def test_trusted_resolver_records_can_corroborate_in_production_mode(self):
        published = NOW - timedelta(days=10)
        catalog = {
            "fixture:a": {
                "content": "The trial supports fracture risk reduction.",
                "resolved_identity": "pmid:1",
                "authority_tier": AuthorityTier.PEER_REVIEWED,
                "published": published,
            },
            "fixture:b": {
                "content": "A second trial supports fracture risk reduction.",
                "resolved_identity": "pmid:2",
                "authority_tier": AuthorityTier.PEER_REVIEWED,
                "published": published,
            },
        }
        resolver = make_inmemory_resolver(catalog, resolver_id="test-catalog")
        leads = [
            Lead.suggest("a", "fixture:a", claimed_published=NOW,
                         suggested_quote="supports fracture risk reduction"),
            Lead.suggest("b", "fixture:b", claimed_published=NOW,
                         suggested_quote="supports fracture risk reduction"),
        ]
        out = verify_text(
            "Vitamin D supports fracture risk reduction.",
            leads,
            (),
            now=NOW,
            resolver_fn=resolver,
            entailment_fn=lambda _content: Entailment.ENTAILS,
            require_provenance=True,
        )
        self.assertEqual(out.decision.status, EvidenceStatus.CORROBORATED)
        self.assertTrue(out.decision.provenance_verified_support)

    def test_trusted_resolver_chain_preserves_source_proof(self):
        published = NOW - timedelta(days=10)
        first = make_inmemory_resolver({}, resolver_id="empty")
        second = make_inmemory_resolver({
            "fixture:b": {
                "content": "A resolver-chain source.",
                "resolved_identity": "pmid:2",
                "authority_tier": AuthorityTier.DATASET,
                "published": published,
            },
        }, resolver_id="catalog")
        chained = chain_resolvers(first, second, resolver_id="approved-chain")
        records, rejected = resolve([
            Lead.suggest("b", "fixture:b", claimed_published=NOW),
        ], resolver_fn=chained)

        self.assertEqual(rejected, [])
        self.assertEqual(records[0].resolved_by, "approved-chain")
        self.assertIsNotNone(records[0].source_proof)

    def test_arbitrary_resolver_cannot_mint_production_provenance(self):
        def arbitrary(lead):
            content = "fabricated resolver content supports claim"
            import hashlib
            return ResolvedSource(
                lineage_id=lead.lineage_id,
                resolved_identity="pmid:fake",
                content=content,
                source_hash=hashlib.sha256(content.encode()).hexdigest(),
                authority_tier=AuthorityTier.PEER_REVIEWED,
                published=NOW - timedelta(days=1),
                resolver_id="attacker",
                binding_ok=True,
                quote="supports claim",
            )

        out = verify_text(
            "The intervention supports claim.",
            [Lead.suggest("a", "pmid:fake", claimed_published=NOW,
                          suggested_quote="supports claim")],
            (),
            now=NOW,
            resolver_fn=arbitrary,
            entailment_fn=lambda _content: Entailment.ENTAILS,
            require_provenance=True,
        )
        self.assertEqual(out.decision.status, EvidenceStatus.UNVERIFIED)

    def test_source_proof_is_bound_to_exact_content(self):
        published = NOW - timedelta(days=1)
        resolver = make_inmemory_resolver({
            "fixture:a": {
                "content": "original bytes",
                "resolved_identity": "pmid:1",
                "authority_tier": AuthorityTier.PEER_REVIEWED,
                "published": published,
            },
        })
        from glass_ionomer.verify import resolve
        records, _ = resolve(
            [Lead.suggest("a", "fixture:a", claimed_published=NOW)],
            resolver_fn=resolver,
        )
        forged = replace(records[0], content="changed bytes")
        out = verify_text(
            "some empirical claim", [forged], (), now=NOW,
            require_provenance=True,
        )
        self.assertEqual(out.decision.status, EvidenceStatus.UNVERIFIED)


class ProductionPresentationTests(unittest.TestCase):
    def test_html_extraction_prefers_article_blocks_over_navigation(self):
        html = "<nav>" + ("navigation " * 100) + "</nav>" + \
            "<article><p>" + ("clinically relevant passage " * 20) + "</p></article>"
        text = _crude_text(html)
        self.assertIn("clinically relevant passage", text)
        self.assertNotIn("navigation", text)


if __name__ == "__main__":
    unittest.main()
