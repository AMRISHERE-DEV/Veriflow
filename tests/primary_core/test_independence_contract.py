"""
Frozen acceptance tests for Project Saturn Verify build #3 — provenance independence in
corroboration counting. Prepared with Claude assistance under Amr Elnaggar's
sole authorship and direction.

CORROBORATION counts INDEPENDENT provenance, not merely distinct lineage_ids:
two supports that resolve to the SAME canonical source (same resolved_identity)
are one independent lineage and top out at SUPPORTED. Strictly additive: with no
independence key, counting falls back to lineage_id (today's behavior). Tagged
buckets keep a key value and an unrelated lineage_id from colliding. The
contradiction side is unchanged (one credible contradiction still contests).

Offline, stdlib only, deterministic.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from project_saturn.verify import (
    AuthorityTier,
    Claim,
    Entailment,
    EvidenceItem,
    EvidenceStatus,
    Lead,
    VerifiabilityClass,
    decide_status,
    extract_claim,
    make_inmemory_resolver,
    verify_extracted,
)

NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)


def _claim():
    return Claim(id="c", text="x", verifiability_class=VerifiabilityClass.EMPIRICAL)


def _item(lineage, *, key=None, stance=Entailment.ENTAILS, strength=1.0):
    return EvidenceItem(lineage_id=lineage, authority_tier=AuthorityTier.PEER_REVIEWED,
                        entailment=stance, integrity_verified=True, fresh=True,
                        applicable=True, strength=strength, independence_key=key,
                        provenance_verified=True)


def _decide(evidence):
    return decide_status(_claim(), [], list(evidence), now=NOW)


def _cat(content, *, ident):
    return {"content": content, "resolved_identity": ident,
            "authority_tier": AuthorityTier.PEER_REVIEWED, "published": NOW - timedelta(days=10)}


def _entails(_content):
    return Entailment.ENTAILS


# 1: back-compat — no independence_key falls back to distinct lineage_id (today's behavior)
def test_independence_key_defaults_to_lineage_id():
    assert _decide([_item("L1"), _item("L2")]).status is EvidenceStatus.CORROBORATED
    assert _decide([_item("L1"), _item("L1")]).status is EvidenceStatus.SUPPORTED


# 2 (headline): two distinct lineages sharing one independence key collapse to one -> SUPPORTED
def test_same_independence_key_collapses_to_supported():
    d = _decide([_item("L1", key="doi:X"), _item("L2", key="doi:X")])
    assert d.support_lineages == 1
    assert d.status is EvidenceStatus.SUPPORTED


# 3: distinct independence keys corroborate
def test_distinct_independence_keys_corroborate():
    d = _decide([_item("L1", key="doi:X"), _item("L2", key="doi:Y")])
    assert d.support_lineages == 2
    assert d.status is EvidenceStatus.CORROBORATED


# 4: provenance can RAISE a mis-counted label (same lineage_id, distinct keys -> 2)
def test_same_lineage_distinct_keys_corroborate():
    d = _decide([_item("L1", key="doi:X"), _item("L1", key="doi:Y")])
    assert d.support_lineages == 2
    assert d.status is EvidenceStatus.CORROBORATED


# 5: tagged buckets - a key "X" and a legacy lineage_id "X" must NOT collide
def test_key_and_lineage_namespace_do_not_collide():
    d = _decide([_item("X", key="X"), _item("X", key=None)])
    assert d.support_lineages == 2
    assert d.status is EvidenceStatus.CORROBORATED


# 6: end-to-end via the build #2 resolver - same resolved_identity -> SUPPORTED, not CORROBORATED
def test_resolver_same_identity_does_not_corroborate():
    cat = {"inline:a": _cat("primary body about the effect", ident="doi:10.1/same"),
           "inline:b": _cat("a press-release mirror of the same paper", ident="doi:10.1/same")}
    r = make_inmemory_resolver(cat)
    leads = [Lead.suggest("lin1", "inline:a", claimed_published=NOW - timedelta(days=10), suggested_quote="body"),
             Lead.suggest("lin2", "inline:b", claimed_published=NOW - timedelta(days=10), suggested_quote="mirror")]
    out = verify_extracted(extract_claim("the effect is real"), leads, [], now=NOW,
                           resolver_fn=r, entailment_fn=_entails)
    assert out.decision.support_lineages == 1
    assert out.decision.status is EvidenceStatus.SUPPORTED


# 7: two leads resolving to DISTINCT identities corroborate
def test_resolver_distinct_identities_corroborate():
    cat = {"inline:a": _cat("paper one body", ident="doi:10.1/one"),
           "inline:b": _cat("paper two body", ident="doi:10.1/two")}
    r = make_inmemory_resolver(cat)
    leads = [Lead.suggest("lin1", "inline:a", claimed_published=NOW - timedelta(days=10), suggested_quote="paper"),
             Lead.suggest("lin2", "inline:b", claimed_published=NOW - timedelta(days=10), suggested_quote="paper")]
    out = verify_extracted(extract_claim("the effect is real"), leads, [], now=NOW,
                           resolver_fn=r, entailment_fn=_entails)
    assert out.decision.support_lineages == 2
    assert out.decision.status is EvidenceStatus.CORROBORATED


# 8: contradiction side unchanged - one credible contradiction contests, independence not required
def test_contradiction_remains_any_one():
    d = _decide([_item("L1", key="doi:X"), _item("L2", key="doi:Y"),
                 _item("L3", key="doi:X", stance=Entailment.CONTRADICTS, strength=0.9)])
    assert d.contested is True
    assert d.status is EvidenceStatus.CONTESTED


# --------------------------------------------------------------------------- #
# Standalone runner
# --------------------------------------------------------------------------- #
def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
    return True


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
