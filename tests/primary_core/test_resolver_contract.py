"""
Frozen acceptance tests for Project Saturn Verify build #2 — the resolver
(lead -> resolved -> admitted). Prepared with Claude assistance under Amr
Elnaggar's sole authorship and direction.

Proves the two target containment rules, mechanically and offline:
  * "lead != evidence": a model-suggested source is a Lead and cannot support a
    claim until a resolver independently confirms it; unresolved leads fail open
    to UNVERIFIED (never REFUTED, never negative).
  * "self-hash != source proof": source-bound provenance and the trusted content/
    hash are reachable ONLY via a resolver that mints the hash from its own bytes.

A Lead's stance never reaches the admitted item (closes both ENTAILS and
CONTRADICTS injection). Strictly additive: resolver_fn=None is byte-compatible.

Runs standalone or under pytest. Offline, stdlib only, deterministic.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from project_saturn.verify import (
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

NOW = datetime(2026, 6, 23, tzinfo=timezone.utc)


def _entails(_content):
    return Entailment.ENTAILS


def _cat(content, *, ident, tier=AuthorityTier.PEER_REVIEWED, age_days=10):
    return {"content": content, "resolved_identity": ident,
            "authority_tier": tier, "published": NOW - timedelta(days=age_days)}


def _rec(lineage, content, *, stance=Entailment.ENTAILS, days=5):
    return EvidenceRecord.of(lineage, content, published=NOW - timedelta(days=days), stance=stance)


# 1: resolved + bound leads can corroborate; their items are source-bound.
def test_resolved_leads_corroborate():
    cat = {
        "inline:k1": _cat("rayleigh scattering makes the daytime sky blue", ident="doi:10.1/k1"),
        "inline:k2": _cat("a second lab independently observed blue-light scattering", ident="doi:10.1/k2"),
    }
    r = make_inmemory_resolver(cat)
    leads = [
        Lead.suggest("lin1", "inline:k1", claimed_published=NOW - timedelta(days=10), suggested_quote="blue"),
        Lead.suggest("lin2", "inline:k2", claimed_published=NOW - timedelta(days=10), suggested_quote="scattering"),
    ]
    ex = extract_claim("the sky scatters blue light")
    out = verify_extracted(ex, leads, [], now=NOW, resolver_fn=r, entailment_fn=_entails)
    assert out.decision.status is EvidenceStatus.CORROBORATED
    records, inadmissible = resolve(leads, resolver_fn=r)
    items = admit(records, now=NOW, entailment_fn=_entails)
    assert inadmissible == []
    assert all(it.integrity_verified and it.source_bound for it in items)


# 2 (headline): a self-hashed record is integrity_verified but NOT source_bound.
def test_self_hash_alone_is_not_source_bound():
    rec = _rec("lin", "FABRICATED CONTENT")
    items = admit([rec], now=NOW)
    assert len(items) == 1
    assert items[0].integrity_verified is True
    assert items[0].source_bound is False


# 3: the resolved record's content + hash are the resolver's bytes, not the lead's.
def test_resolved_content_and_hash_are_the_resolver_bytes():
    content = "the canonical resolver-held passage about X"
    r = make_inmemory_resolver({"inline:k1": _cat(content, ident="doi:10.1/k1")})
    lead = Lead.suggest("lin1", "inline:k1", claimed_published=NOW - timedelta(days=10), suggested_quote="canonical")
    records, inadmissible = resolve([lead], resolver_fn=r)
    assert len(records) == 1 and inadmissible == []
    rec = records[0]
    assert rec.content == content
    assert rec.trusted_hash == hashlib.sha256(content.encode()).hexdigest()
    assert rec.resolved_by is not None


# 4: unknown locator -> UNVERIFIED, never REFUTED, never a contradiction.
def test_unknown_locator_is_unverified_never_refuted():
    r = make_inmemory_resolver({"inline:k1": _cat("known content", ident="doi:10.1/k1")})
    lead = Lead.suggest("lin1", "inline:UNKNOWN", claimed_published=NOW - timedelta(days=10),
                        suggested_quote="x", stance=Entailment.ENTAILS)
    ex = extract_claim("some empirical claim")
    out = verify_extracted(ex, [lead], [], now=NOW, resolver_fn=r, entailment_fn=_entails)
    assert out.decision.status is EvidenceStatus.UNVERIFIED
    assert out.decision.support_lineages == 0
    assert len(out.inadmissible_leads) == 1
    assert out.inadmissible_leads[0].reason == "unknown_locator"


# 5: a real locator but a quote that is not in the source cannot support.
def test_quote_binding_mismatch_cannot_support():
    r = make_inmemory_resolver({"inline:k1": _cat("the source says alpha", ident="doi:10.1/k1")})
    lead = Lead.suggest("lin1", "inline:k1", claimed_published=NOW - timedelta(days=10), suggested_quote="omega")
    ex = extract_claim("some empirical claim")
    out = verify_extracted(ex, [lead], [], now=NOW, resolver_fn=r, entailment_fn=_entails)
    assert out.decision.status is EvidenceStatus.UNVERIFIED
    assert out.decision.support_lineages == 0
    assert any(il.reason == "binding_mismatch" for il in out.inadmissible_leads)


# 6: a model's CONTRADICTS stance on a resolved lead cannot inject a contradiction.
def test_lead_stance_contradicts_cannot_inject_contradiction():
    r = make_inmemory_resolver({"inline:k1": _cat("the source body text", ident="doi:10.1/k1")})
    supports = [_rec("s1", "support one"), _rec("s2", "support two")]
    bad_lead = Lead.suggest("c1", "inline:k1", claimed_published=NOW - timedelta(days=10),
                            suggested_quote="source", stance=Entailment.CONTRADICTS)
    ex = extract_claim("some empirical claim")
    # no entailment_fn: the resolved lead is NEUTRAL (its stance is ignored), the two legacy supports stand
    out = verify_extracted(ex, [*supports, bad_lead], [], now=NOW, resolver_fn=r)
    assert out.decision.status is EvidenceStatus.SUPPORTED
    assert out.decision.contested is False


# 7: model-claimed authority is discarded; the resolver's tier wins.
def test_claimed_authority_is_not_trusted():
    r = make_inmemory_resolver({"inline:k1": _cat("body", ident="doi:10.1/k1", tier=AuthorityTier.WEB)})
    lead = Lead.suggest("lin1", "inline:k1", claimed_published=NOW - timedelta(days=10),
                        suggested_quote="body", claimed_authority=AuthorityTier.AUTHORITATIVE)
    records, _ = resolve([lead], resolver_fn=r)
    assert records[0].authority_tier is AuthorityTier.WEB


# 8: a forged resolved_by yields a (mislabeled) source_bound but NO status power.
def test_forged_resolved_by_grants_no_status_power():
    ex = extract_claim("some empirical claim")
    honest = _rec("lin", "body text")
    forged = EvidenceRecord(
        lineage_id="lin", content="body text", authority_tier=AuthorityTier.PEER_REVIEWED,
        published=NOW - timedelta(days=5), trusted_hash=hashlib.sha256(b"body text").hexdigest(),
        stance=Entailment.ENTAILS, applicable=True, strength=1.0, resolved_by="fake",
    )
    out_honest = verify_extracted(ex, [honest], [], now=NOW)
    out_forged = verify_extracted(ex, [forged], [], now=NOW)
    assert out_honest.decision.status is out_forged.decision.status


# 9: fail-open on total non-resolution; resolver=None remains behaviorally stable; no network.
def test_resolver_fail_open_and_backcompat():
    ex = extract_claim("some empirical claim")
    r = make_inmemory_resolver({})
    leads = [Lead.suggest("l1", "inline:x", claimed_published=NOW - timedelta(days=5),
                          suggested_quote="q", stance=Entailment.ENTAILS)]
    out = verify_extracted(ex, leads, [], now=NOW, resolver_fn=r, entailment_fn=_entails)
    assert out.decision.status is EvidenceStatus.UNVERIFIED
    assert out.decision.support_lineages == 0

    recs = [_rec("s1", "a"), _rec("s2", "b")]
    s_with = verify_extracted(ex, recs, [], now=NOW, resolver_fn=None).decision.status
    s_without = verify_extracted(ex, recs, [], now=NOW).decision.status
    assert s_with is s_without is EvidenceStatus.SUPPORTED

    # Reality Contact v0: networking remains explicit through urllib_fetch. Host resolution
    # exists solely to enforce its public-address SSRF policy; importing or using the offline
    # resolver paths performs no network operation.
    import project_saturn.verify.resolver as _res
    src = inspect.getsource(_res)
    assert "socket.getaddrinfo" in src
    assert src.index("socket.getaddrinfo") > src.index("def _resolve_host")
    assert src.index("socket.getaddrinfo") < src.index("def urllib_fetch")


# 10: a contradiction is legitimate ONLY when entailment_fn computes it from the
# resolver's content - never from the lead's advisory stance (symmetric to test 6).
def test_resolver_content_can_drive_contradiction_via_entailment_fn():
    r = make_inmemory_resolver({"inline:k1": _cat("the source refutes the claim", ident="doi:10.1/k1")})
    supports = [_rec("s1", "support one"), _rec("s2", "support two")]
    lead = Lead.suggest("c1", "inline:k1", claimed_published=NOW - timedelta(days=10),
                        suggested_quote="refutes", stance=Entailment.NEUTRAL)
    ex = extract_claim("some empirical claim")
    def contra(content):
        return Entailment.CONTRADICTS if "refutes" in content else Entailment.ENTAILS
    out = verify_extracted(ex, [*supports, lead], [], now=NOW, resolver_fn=r, entailment_fn=contra)
    assert out.decision.contested is True   # entailment on resolver bytes drives a credible contradiction


# 11: in one run, source_bound is True for a resolver-resolved item, False for a legacy .of() record.
def test_mixed_resolved_and_legacy_source_bound():
    r = make_inmemory_resolver({"inline:k1": _cat("resolver body about X", ident="doi:10.1/k1")})
    lead = Lead.suggest("lin1", "inline:k1", claimed_published=NOW - timedelta(days=10), suggested_quote="body")
    legacy = _rec("leg1", "legacy supporting passage")
    records, inadmissible = resolve([lead, legacy], resolver_fn=r)
    items = {it.lineage_id: it for it in admit(records, now=NOW, entailment_fn=_entails)}
    assert inadmissible == []
    assert items["lin1"].source_bound is True
    assert items["leg1"].source_bound is False


# 12: ENTAILS side of test 6 - a resolved lead's ENTAILS stance is ignored; with no
# entailment_fn the admitted item is NEUTRAL and cannot support.
def test_resolved_lead_entails_stance_ignored_without_entailment_fn():
    r = make_inmemory_resolver({"inline:k1": _cat("the source body text", ident="doi:10.1/k1")})
    lead = Lead.suggest("lin1", "inline:k1", claimed_published=NOW - timedelta(days=10),
                        suggested_quote="source", stance=Entailment.ENTAILS)
    ex = extract_claim("some empirical claim")
    out = verify_extracted(ex, [lead], [], now=NOW, resolver_fn=r)  # no entailment_fn
    assert out.decision.status is EvidenceStatus.UNVERIFIED
    assert out.decision.support_lineages == 0


# 13: an unresolved lead cannot contradict even if entailment_fn would say CONTRADICTS
# (it is dropped as inadmissible before any entailment is consumed).
def test_unresolved_lead_cannot_contradict_even_with_contradicting_entailment_fn():
    r = make_inmemory_resolver({})  # every locator unknown
    lead = Lead.suggest("u1", "inline:missing", claimed_published=NOW - timedelta(days=5),
                        suggested_quote="q", stance=Entailment.CONTRADICTS)
    ex = extract_claim("some empirical claim")
    out = verify_extracted(ex, [lead], [], now=NOW, resolver_fn=r,
                           entailment_fn=lambda content: Entailment.CONTRADICTS)
    assert out.decision.status is EvidenceStatus.UNVERIFIED   # not REFUTED, not CONTESTED
    assert out.decision.contested is False
    assert out.decision.support_lineages == 0


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
