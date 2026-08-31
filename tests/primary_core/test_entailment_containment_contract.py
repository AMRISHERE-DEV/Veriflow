"""
Frozen acceptance tests for Slice 1 — resolver-path model-entailment containment.
Prepared with Claude assistance under Amr Elnaggar's sole authorship and direction.

Closes the leak: a model-supplied entailment (entailment_fn) over RESOLVER-sourced
bytes must NOT be able to manufacture support/contradiction unless it is carried by
a NON-EMPTY quote that binds to the source content. Quote-binding is NECESSARY (the
passage must exist in the source) but NOT sufficient (entailment stays the model's
separate judgment). The fix: carry Lead.suggested_quote through
resolver -> ResolvedSource -> EvidenceRecord, and demote a model-derived stance to
NEUTRAL on a resolver-sourced record whose quote is empty or does not bind.

Legacy (non-resolver) records and quote-bearing resolver leads are UNCHANGED.

Offline, stdlib only, deterministic.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from veriflow.verify import (
    AuthorityTier,
    Entailment,
    EvidenceRecord,
    EvidenceStatus,
    Lead,
    admit,
    extract_claim,
    make_inmemory_resolver,
    resolve,
    verify_extracted,
)

NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)


def _cat(content, *, ident):
    return {"content": content, "resolved_identity": ident,
            "authority_tier": AuthorityTier.PEER_REVIEWED, "published": NOW - timedelta(days=10)}


def _lead(lineage, locator, *, quote):
    return Lead.suggest(lineage, locator, claimed_published=NOW - timedelta(days=10), suggested_quote=quote)


def _entails(_content):
    return Entailment.ENTAILS


# the resolver now carries the lead's suggested_quote onto the admitted record
def test_resolver_carries_suggested_quote_onto_record():
    r = make_inmemory_resolver({"inline:k1": _cat("body about blue light", ident="doi:1")})
    records, _ = resolve([_lead("l1", "inline:k1", quote="blue")], resolver_fn=r)
    assert records[0].quote == "blue"


# 1: resolved lead with a VALID suggested_quote + entailment_fn CAN support
def test_resolved_valid_quote_entailment_can_support():
    cat = {"inline:k1": _cat("the sky scatters blue light", ident="doi:1"),
           "inline:k2": _cat("an independent report of blue scattering", ident="doi:2")}
    r = make_inmemory_resolver(cat)
    leads = [_lead("l1", "inline:k1", quote="blue"), _lead("l2", "inline:k2", quote="scattering")]
    out = verify_extracted(extract_claim("the sky scatters blue light"), leads, [], now=NOW,
                           resolver_fn=r, entailment_fn=_entails)
    assert out.decision.status is EvidenceStatus.CORROBORATED


# 2 (the leak fix): resolved lead with an EMPTY suggested_quote + entailment_fn CANNOT support
def test_resolved_empty_quote_entailment_cannot_support():
    cat = {"inline:k1": _cat("the sky scatters blue light", ident="doi:1"),
           "inline:k2": _cat("more about blue light", ident="doi:2")}
    r = make_inmemory_resolver(cat)
    leads = [_lead("l1", "inline:k1", quote=""), _lead("l2", "inline:k2", quote="")]
    out = verify_extracted(extract_claim("the sky scatters blue light"), leads, [], now=NOW,
                           resolver_fn=r, entailment_fn=_entails)
    assert out.decision.status is EvidenceStatus.UNVERIFIED
    assert out.decision.support_lineages == 0


# 3: resolved lead with a FABRICATED suggested_quote CANNOT support
def test_resolved_fabricated_quote_cannot_support():
    r = make_inmemory_resolver({"inline:k1": _cat("the sky scatters blue light", ident="doi:1")})
    leads = [_lead("l1", "inline:k1", quote="a totally fabricated passage")]
    out = verify_extracted(extract_claim("the sky scatters blue light"), leads, [], now=NOW,
                           resolver_fn=r, entailment_fn=_entails)
    assert out.decision.status is EvidenceStatus.UNVERIFIED
    assert out.decision.support_lineages == 0


# 4: legacy quoteless EvidenceRecord.of(..., quote="") still behaves as before (back-compat)
def test_legacy_quoteless_record_unchanged():
    recs = [EvidenceRecord.of("L1", "a", published=NOW - timedelta(days=5), stance=Entailment.ENTAILS),
            EvidenceRecord.of("L2", "b", published=NOW - timedelta(days=5), stance=Entailment.ENTAILS)]
    out = verify_extracted(extract_claim("a claim"), recs, [], now=NOW)  # no resolver, no entailment_fn
    # Self-hashed caller records retain support semantics but cannot corroborate.
    assert out.decision.status is EvidenceStatus.SUPPORTED


# 5: a model entailment can demote/neutralize, but cannot CREATE support without a quote-bound span
def test_model_entailment_cannot_create_support_without_bound_quote():
    r = make_inmemory_resolver({"inline:k1": _cat("the sky scatters blue light", ident="doi:1")})
    records, _ = resolve([_lead("l1", "inline:k1", quote="")], resolver_fn=r)  # resolved, empty quote
    items = admit(records, now=NOW, entailment_fn=_entails)
    # resolver-sourced + entailment_fn + empty quote -> demoted to NEUTRAL (cannot support/contradict)
    assert all(it.entailment is Entailment.NEUTRAL for it in items)


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
