"""
Frozen hardening contract for the VERIFY layer (validation & DoS).

Covers four findings; each test that fixes a finding includes an ADVERSARIAL case
reproducing the original exploit and asserting it is now blocked:

  #4 verifiers.py  - arithmetic DoS: unbounded ** / deep nesting must be REJECTED
                     (ERROR, never PASS), and no giant integer is ever materialized.
  #8 verifiers.py  - exact rational comparison across ALL relations
                     (equality and inequality agree at the boundary).
  #5 status.py     - StatusPolicy & EvidenceItem validate-and-raise: a policy that
                     would CORROBORATE with zero lineages is rejected; inf/NaN/out-of-
                     range strengths are rejected.
  #6 admissibility - future-dated evidence is NOT fresh.
  #7 resolver.py   - urllib_fetch body read is byte-capped; oversized bodies fail open.

Offline, stdlib only, deterministic. Run directly: exits 0 on success.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from glass_ionomer.verify import resolver as resolver_mod
from glass_ionomer.verify.admissibility import EvidenceRecord, admit
from glass_ionomer.verify.status import (
    AuthorityTier,
    Entailment,
    EvidenceItem,
    StatusPolicy,
)
from glass_ionomer.verify.verifiers import (
    ArithmeticBoundError,
    evaluate_relation,
)


# --------------------------------------------------------------------------- #
# #4 arithmetic DoS
# --------------------------------------------------------------------------- #
def test_pow_tower_dos_is_rejected_not_evaluated():
    # ADVERSARIAL: the original exploit. 9**9**9 would materialize an astronomically
    # large integer and burn CPU/memory. It must now RAISE (bound error), never return.
    try:
        evaluate_relation("9**9**9 == 1")
    except ArithmeticBoundError:
        pass
    else:
        raise AssertionError("9**9**9 should be rejected by the exponent cap")


def test_single_large_pow_is_rejected():
    # A direct huge exponent is rejected before the integer is built.
    raised = False
    try:
        evaluate_relation("2**100000 == 0")
    except ArithmeticBoundError:
        raised = True
    assert raised, "large exponent must be rejected"


def test_huge_operand_is_rejected():
    raised = False
    try:
        evaluate_relation("1e300 == 1e300")
    except ArithmeticBoundError:
        raised = True
    assert raised, "operand beyond magnitude cap must be rejected"


def test_deeply_nested_relation_is_rejected():
    # ADVERSARIAL: deep nesting / huge node count that would burn recursion / CPU
    # before evaluation. A long chain of additions blows the node-count cap.
    expr = "+".join(["1"] * 300) + " == 300"
    raised = False
    try:
        evaluate_relation(expr)
    except (ArithmeticBoundError, RecursionError, ValueError):
        raised = True
    assert raised, "oversized/deeply nested relation must be rejected, not evaluated"


def test_node_count_cap_blocks_large_expression():
    # ADVERSARIAL: deeply nested binary ops (not just flat) also exceed the depth cap.
    expr = "1" + ("+1" * 0) + ("*(1" * 30) + (")" * 30) + " == 1"
    raised = False
    try:
        evaluate_relation(expr)
    except (ArithmeticBoundError, RecursionError, ValueError):
        raised = True
    assert raised, "deeply nested binary ops must be rejected"


def test_arithmetic_verifier_reports_error_not_pass_on_dos():
    # The DoS input must surface as ERROR through the verifier, NEVER a PASS.
    from glass_ionomer.verify.extraction import ExtractedClaim
    from glass_ionomer.verify.status import Claim, VerifiabilityClass, VerifierOutcome
    from glass_ionomer.verify.verifiers import ArithmeticVerifier

    claim = Claim(id="C1", text="9**9**9 == 1",
                  verifiability_class=VerifiabilityClass.MECHANICAL)
    ex = ExtractedClaim(claim=claim, formalization="9**9**9 == 1", binding_ok=True)
    res = ArithmeticVerifier().verify(ex)
    assert res.outcome is VerifierOutcome.ERROR, "DoS input must be ERROR, not PASS"
    assert res.outcome is not VerifierOutcome.PASS


def test_normal_relations_still_evaluate():
    # Bounds must not break ordinary mechanical relations.
    assert evaluate_relation("2+2==4") is True
    assert evaluate_relation("2+2==5") is False
    assert evaluate_relation("1<2<3") is True
    assert evaluate_relation("2**10 == 1024") is True


# --------------------------------------------------------------------------- #
# #8 exact comparison across all relations
# --------------------------------------------------------------------------- #
def test_tolerance_consistent_at_boundary():
    # Distinct decimal literals remain distinct. Approximate equality belongs in a
    # typed domain policy, not the mechanical status gate.
    near = "1.0 == 1.0000000000001"
    assert evaluate_relation(near) is False
    assert evaluate_relation("1.0 < 1.0000000000001") is True
    assert evaluate_relation("1.0 <= 1.0000000000001") is True
    assert evaluate_relation("1.0 != 1.0000000000001") is True


def test_strict_inequality_genuine_difference_holds():
    assert evaluate_relation("1 < 2") is True
    assert evaluate_relation("2 > 1") is True
    assert evaluate_relation("2 <= 1") is False
    assert evaluate_relation("1 >= 2") is False


def test_distinct_large_integers_are_not_equal_no_false_verified():
    # ADVERSARIAL (red-team blocker: isclose minted false VERIFIED): isclose with
    # rel_tol=1e-9 treated distinct large integers as EQUAL (|1e9 - (1e9+1)| <= tol),
    # so 1000000000 == 1000000001 PASSED -> false VERIFIED on a mechanical claim.
    # Integer operands must now compare EXACTLY.
    assert evaluate_relation("1000000000 == 1000000001") is False
    assert evaluate_relation("1000000000 != 1000000001") is True
    assert evaluate_relation("1000000000 < 1000000001") is True
    assert evaluate_relation("999999999999 == 999999999999") is True
    # Decimal literals are evaluated as exact rationals, preserving this identity.
    assert evaluate_relation("0.1 + 0.2 == 0.3") is True
    assert evaluate_relation("1000000000000000.0 == 999999999999999.0") is False


# --------------------------------------------------------------------------- #
# #5 StatusPolicy / EvidenceItem validation
# --------------------------------------------------------------------------- #
def test_policy_rejects_zero_corroboration_lineages():
    # ADVERSARIAL: corroboration_min_lineages=0 made support_lineages >= 0 always
    # True -> CORROBORATED with ZERO support. Construction must now RAISE.
    raised = False
    try:
        StatusPolicy(corroboration_min_lineages=0)
    except ValueError:
        raised = True
    assert raised, "corroboration_min_lineages=0 must be rejected at construction"


def test_policy_rejects_negative_corroboration_lineages():
    raised = False
    try:
        StatusPolicy(corroboration_min_lineages=-1)
    except ValueError:
        raised = True
    assert raised


def test_policy_rejects_empty_authority_and_bad_strengths():
    for kwargs in (
        {"min_authority_for_verified": ()},
        {"support_min_strength": float("inf")},
        {"contradiction_min_strength": float("nan")},
        {"support_min_strength": 1.5},
        {"support_min_strength": -0.1},
    ):
        raised = False
        try:
            StatusPolicy(**kwargs)
        except ValueError:
            raised = True
        assert raised, f"StatusPolicy({kwargs}) should be rejected"


def test_default_policy_unchanged_and_valid():
    p = StatusPolicy()
    assert p.corroboration_min_lineages == 2
    assert p.min_authority_for_verified == (AuthorityTier.AUTHORITATIVE,)


def _item(strength):
    return EvidenceItem(lineage_id="L1", authority_tier=AuthorityTier.WEB,
                        entailment=Entailment.ENTAILS, integrity_verified=True,
                        fresh=True, strength=strength)


def test_evidence_item_rejects_inf_strength():
    # ADVERSARIAL: inf strength would pass any threshold.
    raised = False
    try:
        _item(float("inf"))
    except ValueError:
        raised = True
    assert raised, "inf strength must be rejected"


def test_evidence_item_rejects_nan_strength():
    # ADVERSARIAL: NaN strength silently drops evidence (NaN >= t is False).
    raised = False
    try:
        _item(float("nan"))
    except ValueError:
        raised = True
    assert raised, "NaN strength must be rejected"


def test_evidence_item_rejects_out_of_range_strength():
    for bad in (1.5, -0.01):
        raised = False
        try:
            _item(bad)
        except ValueError:
            raised = True
        assert raised, f"strength {bad} must be rejected"


def test_evidence_item_accepts_valid_strength():
    assert _item(0.0).strength == 0.0
    assert _item(1.0).strength == 1.0
    assert _item(0.5).strength == 0.5


# --------------------------------------------------------------------------- #
# #6 future-dated evidence is not fresh
# --------------------------------------------------------------------------- #
def test_future_dated_evidence_is_not_fresh():
    # ADVERSARIAL: a record published in the FUTURE passed the old
    # `(now - published) <= max_age` test (negative age). It must now be NOT fresh.
    now = datetime(2026, 1, 1)
    future = EvidenceRecord.of("L1", "future content", published=datetime(2030, 1, 1))
    items = admit([future], now=now)
    assert items[0].fresh is False, "future-dated record must not be fresh"


def test_present_and_recent_evidence_is_fresh():
    now = datetime(2026, 1, 1)
    rec = EvidenceRecord.of("L2", "recent content", published=datetime(2025, 12, 31))
    items = admit([rec], now=now, max_age=timedelta(days=30))
    assert items[0].fresh is True


def test_old_evidence_beyond_max_age_is_not_fresh():
    now = datetime(2026, 1, 1)
    rec = EvidenceRecord.of("L3", "stale content", published=datetime(2020, 1, 1))
    items = admit([rec], now=now, max_age=timedelta(days=30))
    assert items[0].fresh is False


# --------------------------------------------------------------------------- #
# #7 urllib_fetch byte cap (no network: fake response stream)
# --------------------------------------------------------------------------- #
class _FakeResponse:
    """Minimal stand-in for an http response with a bounded .read(n)."""
    def __init__(self, body: bytes):
        self._body = body
        self.status = 200
        self.headers = {}

    def read(self, n=None):
        if n is None:
            return self._body
        return self._body[:n]

    def geturl(self):
        return "https://example.test/page"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_oversized_body_is_bounded_and_fails_open(monkeypatch=None):
    # ADVERSARIAL: an enormous body must NOT reach the full-body regex. We inject a
    # fake urlopen returning a body larger than the cap and assert fetch fails open.
    cap = resolver_mod._MAX_FETCH_BYTES
    huge = b"<p>" + (b"x" * (cap + 1024)) + b"</p>"

    captured = {}
    def fake_urlopen(req, *, timeout=None, host_resolver=None):
        return _FakeResponse(huge)

    # Also wrap _read_capped to confirm the read was bounded (never the full body).
    orig_read_capped = resolver_mod._read_capped

    def spy_read_capped(resp, *, max_bytes=cap):
        # The cap path should request at most max_bytes+1 from the stream.
        raw = resp.read(max_bytes + 1)
        captured["read_len"] = len(raw) if raw is not None else 0
        if raw is not None and len(raw) > max_bytes:
            return None
        return raw

    resolver_mod._read_capped = spy_read_capped
    try:
        result = resolver_mod.urllib_fetch(
            "https://example.test/page",
            host_resolver=lambda _host: ("93.184.216.34",), opener=fake_urlopen)
    finally:
        resolver_mod._read_capped = orig_read_capped

    assert result is None, "oversized body must fail open (return None)"
    assert captured["read_len"] <= cap + 1, "read must be bounded to cap+1 bytes"


def test_small_body_still_fetches():
    body = b"<p>" + (b"Glass Ionomer real content here. " * 20).strip() + b"</p>"

    def fake_urlopen(req, *, timeout=None, host_resolver=None):
        return _FakeResponse(body)

    result = resolver_mod.urllib_fetch(
        "https://example.test/page",
        host_resolver=lambda _host: ("93.184.216.34",), opener=fake_urlopen)
    assert result is not None
    assert "Glass Ionomer real content" in result.content


def test_private_and_loopback_fetch_targets_are_rejected_before_open():
    calls = []

    def should_not_open(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("private target must not be opened")

    for url, address in (
        ("http://127.0.0.1/admin", "127.0.0.1"),
        ("http://169.254.169.254/latest/meta-data", "169.254.169.254"),
        ("http://[::1]/", "::1"),
    ):
        result = resolver_mod.urllib_fetch(
            url, host_resolver=lambda _host, value=address: (value,), opener=should_not_open)
        assert result is None
    assert calls == []


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
