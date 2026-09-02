"""
Frozen acceptance tests for Glass Ionomer build #4 - quote/span-bound entailment.
Prepared with Claude assistance under Amr Elnaggar's sole authorship and direction.

A support/contradiction only counts if the cited passage actually appears in the
admitted content. A fabricated (non-matching) quote DEMOTES the stance to NEUTRAL
(subtract only, never invert) - it can never create support, a contradiction, or
REFUTED. Quote-binding proves the passage EXISTS; it does NOT prove entailment.
Strictly additive: a quoteless record (quote="") is bound and behaves as before.

Offline, stdlib only, deterministic.
"""
from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from glass_ionomer.verify import (
    AuthorityTier,
    Claim,
    Direction,
    Entailment,
    EvidenceItem,
    EvidenceRecord,
    EvidenceStatus,
    VerifiabilityClass,
    admit,
    decide_status,
)

NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)


def _claim():
    return Claim(id="c1", text="x", verifiability_class=VerifiabilityClass.EMPIRICAL)


def _rec(lineage, content, *, stance=Entailment.ENTAILS, quote="", strength=1.0, days=5):
    return EvidenceRecord.of(lineage, content, published=NOW - timedelta(days=days),
                             stance=stance, quote=quote, strength=strength)


def _decide(records):
    items = [replace(item, provenance_verified=True) for item in admit(records, now=NOW)]
    return decide_status(_claim(), [], items, now=NOW)


def _item(lineage, *, entail=Entailment.ENTAILS, quote_bound=True, strength=1.0):
    return EvidenceItem(lineage_id=lineage, authority_tier=AuthorityTier.PEER_REVIEWED,
                        entailment=entail, integrity_verified=True, fresh=True,
                        strength=strength, quote_bound=quote_bound,
                        provenance_verified=True)


# 1: legacy quoteless records may support but cannot establish provenance
def test_no_quote_legacy_counts():
    items = admit([_rec("L1", "alpha source"), _rec("L2", "beta source")], now=NOW)
    assert all(it.quote_bound for it in items)
    assert decide_status(_claim(), [], items, now=NOW).status is EvidenceStatus.SUPPORTED


# 2: a matching quote (case/whitespace-insensitive) counts; stance preserved
def test_matching_quote_counts():
    items = admit([_rec("L1", "...the study found x in mice...", quote="The  STUDY found X")], now=NOW)
    assert items[0].quote_bound is True and items[0].entailment is Entailment.ENTAILS
    assert decide_status(_claim(), [], items, now=NOW).status is EvidenceStatus.SUPPORTED


# 3: a fabricated quote cannot support - demoted to NEUTRAL, excluded
def test_fabricated_quote_cannot_support():
    items = admit([_rec("L1", "content about apples", quote="oranges are mentioned")], now=NOW)
    assert items[0].quote_bound is False and items[0].entailment is Entailment.NEUTRAL
    d = decide_status(_claim(), [], items, now=NOW)
    assert d.status is EvidenceStatus.UNVERIFIED  # never SUPPORTED, never REFUTED


# 4: a fabricated contradiction cannot contest a genuinely corroborated claim
def test_fabricated_contradiction_cannot_contest():
    d = _decide([_rec("L1", "alpha"), _rec("L2", "beta"),
                 _rec("C1", "gamma source", stance=Entailment.CONTRADICTS,
                      quote="fabricated passage", strength=0.9)])
    assert d.status is EvidenceStatus.CORROBORATED and d.contested is False


# 5: quote binds but stance is NEUTRAL -> still does not support (binding != entailment)
def test_quote_binds_but_neutral_does_not_support():
    items = admit([_rec("L1", "the study text here", stance=Entailment.NEUTRAL, quote="study")], now=NOW)
    assert items[0].quote_bound is True
    assert decide_status(_claim(), [], items, now=NOW).status is EvidenceStatus.UNVERIFIED


# 6: a lone fabricated contradiction yields zero negative pressure (fail-open floor)
def test_fail_open_floor():
    d = _decide([_rec("L1", "x content", stance=Entailment.CONTRADICTS, quote="not here", strength=0.9)])
    assert d.status is EvidenceStatus.UNVERIFIED
    assert d.direction is not Direction.REFUTES  # never REFUTED, never refuting direction


# 7: hand-built EvidenceItems that bypass admit() are still gated by the decide_status conjunct
def test_handbuilt_unbound_item_is_not_counted():
    # an unbound ENTAILS item must not support
    d = decide_status(_claim(), [], [_item("L1", entail=Entailment.ENTAILS, quote_bound=False)], now=NOW)
    assert d.status is EvidenceStatus.UNVERIFIED
    # an unbound CONTRADICTS item must not contest two genuine (bound) supports
    d2 = decide_status(_claim(), [],
                       [_item("L1"), _item("L2"),
                        _item("C1", entail=Entailment.CONTRADICTS, quote_bound=False, strength=0.9)],
                       now=NOW)
    assert d2.status is EvidenceStatus.CORROBORATED and d2.contested is False


# 8: provenance fields append after quote fields (positional-construction / ABI guard)
def test_new_fields_are_final():
    assert [f.name for f in dataclasses.fields(EvidenceRecord)[-3:]] == [
        "quote", "model_derived_stance", "source_proof"]
    assert [f.name for f in dataclasses.fields(EvidenceItem)[-3:]] == [
        "quote_bound", "model_derived_stance", "provenance_verified"]


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
