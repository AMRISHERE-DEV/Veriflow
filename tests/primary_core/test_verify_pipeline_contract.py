"""
End-to-end tests for the spine: extraction + binding, verifiers, admissibility,
and the wired pipeline. Runs standalone or under pytest.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from veriflow.verify.admissibility import EvidenceRecord
from veriflow.verify.extraction import extract_claim
from veriflow.verify.pipeline import verify_extracted, verify_text
from veriflow.verify.status import (
    AuthorityTier,
    Claim,
    Entailment,
    EvidenceStatus,
    VerifiabilityClass,
)
from veriflow.verify.verifiers import (
    ArithmeticVerifier,
    LlmAssertVerifier,
    RegistryVerifier,
    _issue_trusted_registry_record,
    evaluate_relation,
)

NOW = datetime(2026, 6, 23, tzinfo=timezone.utc)
ARITH = [ArithmeticVerifier()]


def support(lineage, *, days=10, stance=Entailment.ENTAILS, strength=1.0, integrity=True):
    return EvidenceRecord.of(lineage, f"content-{lineage}", published=NOW - timedelta(days=days),
                             stance=stance, strength=strength, integrity=integrity)


# --- extraction + binding ------------------------------------------------- #
def test_extraction_classifies_arithmetic_as_mechanical():
    ex = extract_claim("3 * 5 = 15")
    assert ex.claim.verifiability_class is VerifiabilityClass.MECHANICAL
    assert ex.formalization == "3*5==15"
    assert ex.binding_ok is True


def test_extraction_classifies_opinion_as_interpretive():
    ex = extract_claim("Python is the best language")
    assert ex.claim.verifiability_class is VerifiabilityClass.INTERPRETIVE


def test_extraction_defaults_to_empirical():
    ex = extract_claim("The lake bloom is driven by phosphorus")
    assert ex.claim.verifiability_class is VerifiabilityClass.EMPIRICAL


def test_measurable_comparison_is_not_forced_into_opinion_ceiling():
    ex = extract_claim("Treatment A reduces mortality better than treatment B")
    assert ex.claim.verifiability_class is VerifiabilityClass.EMPIRICAL


def test_evaluate_relation_chained():
    assert evaluate_relation("1<2<3") is True
    assert evaluate_relation("2+2==4") is True
    assert evaluate_relation("2+2==5") is False


# --- arithmetic verifier through the pipeline ----------------------------- #
def test_true_arithmetic_is_verified():
    out = verify_text("12 / 4 = 3", [], ARITH, now=NOW)
    assert out.decision.status is EvidenceStatus.VERIFIED


def test_false_arithmetic_is_refuted():
    out = verify_text("2 ** 3 = 9", [], ARITH, now=NOW)
    assert out.decision.status is EvidenceStatus.REFUTED


def test_correct_math_but_failed_binding_is_not_verified():
    # Simulate a mis-extraction: the formalization evaluates True, but it does not
    # round-trip to the claim, so binding_ok=False -> must NOT reach VERIFIED.
    ex = extract_claim("2 + 2 = 4")
    broken = type(ex)(claim=ex.claim, formalization="2+2==4", binding_ok=False,
                      binding_note="simulated mis-extraction")
    out = verify_extracted(broken, [], ARITH, now=NOW)
    assert out.decision.status is not EvidenceStatus.VERIFIED
    assert any("formalization" in r.lower() for r in out.decision.reasons)


# --- registry verifier ---------------------------------------------------- #
def _registry():
    return RegistryVerifier({"x": _issue_trusted_registry_record("approved", AuthorityTier.AUTHORITATIVE)})


def test_registry_match_is_verified():
    out = verify_text("lookup:x=approved", [], [_registry()], now=NOW)
    assert out.decision.status is EvidenceStatus.VERIFIED


def test_registry_mismatch_is_refuted():
    out = verify_text("lookup:x=denied", [], [_registry()], now=NOW)
    assert out.decision.status is EvidenceStatus.REFUTED


def test_registry_unknown_key_is_unverified():
    out = verify_text("lookup:y=approved", [], [_registry()], now=NOW)
    assert out.decision.status is EvidenceStatus.UNVERIFIED


# --- empirical evidence paths --------------------------------------------- #
def test_two_independent_supports_corroborated():
    out = verify_text("empirical claim", [support("A"), support("B")], [], now=NOW)
    assert out.decision.status is EvidenceStatus.SUPPORTED


def test_support_plus_contradiction_contested():
    out = verify_text("empirical claim",
                      [support("A"), support("B", stance=Entailment.CONTRADICTS, strength=0.9)],
                      [], now=NOW)
    assert out.decision.status is EvidenceStatus.CONTESTED


def test_stale_support_is_dropped():
    out = verify_text("empirical claim", [support("A", days=4000)], [], now=NOW,
                      max_age=timedelta(days=365))
    assert out.decision.status is EvidenceStatus.UNVERIFIED


def test_integrity_failure_is_dropped():
    out = verify_text("empirical claim",
                      [support("A", integrity=False), support("B", integrity=False)],
                      [], now=NOW)
    assert out.decision.status is EvidenceStatus.UNVERIFIED


# --- the non-LLM rule, end to end ----------------------------------------- #
def test_llm_only_pass_on_mechanical_claim_not_verified():
    mech = Claim(id="m", text="The 500th digit of pi is 2",
                 verifiability_class=VerifiabilityClass.MECHANICAL)
    ex = extract_claim("placeholder")
    ex = type(ex)(claim=mech, formalization=None, binding_ok=False, binding_note="")
    out = verify_extracted(ex, [], [LlmAssertVerifier()], now=NOW)
    assert out.decision.status is not EvidenceStatus.VERIFIED
    assert any("non-LLM rule" in r for r in out.decision.reasons)


# --- standalone runner ---------------------------------------------------- #
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
