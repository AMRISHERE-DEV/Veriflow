"""
Verifiers for the spine.

Two are GENUINELY non-LLM and can ground `Verified`:
  * ArithmeticVerifier  - deterministically evaluates a parsed arithmetic relation.
  * RegistryVerifier    - matches a claim against an authoritative structured table.

One is illustrative and, by policy, can NEVER ground `Verified`:
  * LlmAssertVerifier   - represents an LLM "verifying" a claim; useful for showing
                          the non-LLM rule in action.

Crucially, ArithmeticVerifier carries the extraction's binding result into
`formalization_checked`: a correct evaluation of a *wrongly extracted*
formalization will PASS the math but still cannot reach `Verified`.

Stdlib only.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .extraction import ExtractedClaim
from .safe_arith import ArithmeticBoundError as ArithmeticBoundError
from .safe_arith import evaluate_relation
from .status import (
    AuthorityTier,
    VerifiabilityClass,
    VerifierKind,
    VerifierOutcome,
    VerifierResult,
)


@runtime_checkable
class Verifier(Protocol):
    verifier_id: str
    def applies_to(self, ex: ExtractedClaim) -> bool: ...
    def verify(self, ex: ExtractedClaim) -> VerifierResult: ...


class ArithmeticVerifier:
    verifier_id = "arith"

    def applies_to(self, ex: ExtractedClaim) -> bool:
        return (ex.claim.verifiability_class is VerifiabilityClass.MECHANICAL
                and ex.formalization is not None)

    def verify(self, ex: ExtractedClaim) -> VerifierResult:
        formalization = ex.formalization
        if formalization is None:
            return VerifierResult(
                self.verifier_id,
                VerifierKind.DETERMINISTIC,
                VerifierOutcome.INCONCLUSIVE,
                is_llm=False,
                formalization_checked=False,
                detail="no arithmetic formalization available",
            )
        try:
            result = evaluate_relation(formalization)
        except Exception as err:
            return VerifierResult(self.verifier_id, VerifierKind.DETERMINISTIC,
                                  VerifierOutcome.ERROR, is_llm=False,
                                  formalization_checked=ex.binding_ok, detail=str(err))
        outcome = VerifierOutcome.PASS if result else VerifierOutcome.FAIL
        return VerifierResult(self.verifier_id, VerifierKind.DETERMINISTIC, outcome,
                              is_llm=False, formalization_checked=ex.binding_ok,
                              detail=f"evaluated {formalization} -> {result}")


# --------------------------------------------------------------------------- #
# Authoritative registry lookup
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RegistryRecord:
    value: str
    authority_tier: AuthorityTier
    content_hash: str  # sha256 hex of the authoritative value (integrity anchor)

    @staticmethod
    def of(value: str, authority_tier: AuthorityTier = AuthorityTier.AUTHORITATIVE) -> RegistryRecord:
        """Compatibility constructor for caller data; it grants no authority."""
        return untrusted_registry_record(value, authority_tier)


_REGISTRY_TRUST_TOKEN = object()


def _issue_trusted_registry_record(
    value: str,
    authority_tier: AuthorityTier = AuthorityTier.AUTHORITATIVE,
) -> RegistryRecord:
    """Internal resolver/fixture boundary for an authoritative registry snapshot."""
    rec = RegistryRecord(value=value, authority_tier=authority_tier,
                         content_hash=hashlib.sha256(value.encode()).hexdigest())
    object.__setattr__(rec, "_trust_token", _REGISTRY_TRUST_TOKEN)
    return rec


def untrusted_registry_record(value: str,
                              authority_tier: AuthorityTier = AuthorityTier.AUTHORITATIVE) -> RegistryRecord:
    """Build a record from caller/context data without granting authoritative integrity."""
    return RegistryRecord(value=value, authority_tier=authority_tier,
                          content_hash=hashlib.sha256(value.encode()).hexdigest())


def registry_record_is_trusted(record: RegistryRecord) -> bool:
    return getattr(record, "_trust_token", None) is _REGISTRY_TRUST_TOKEN


class RegistryVerifier:
    """Handles claims of the form `lookup:<key>=<expected>`."""
    verifier_id = "registry"
    _PREFIX = "lookup:"

    def __init__(self, registry: dict):
        self.registry = registry

    def applies_to(self, ex: ExtractedClaim) -> bool:
        return ex.claim.text.strip().lower().startswith(self._PREFIX)

    def verify(self, ex: ExtractedClaim) -> VerifierResult:
        body = ex.claim.text.strip()[len(self._PREFIX):]
        if "=" not in body:
            return VerifierResult(self.verifier_id, VerifierKind.AUTHORITATIVE_LOOKUP,
                                  VerifierOutcome.ERROR, is_llm=False, detail="malformed lookup")
        key, _, expected = body.partition("=")
        key, expected = key.strip(), expected.strip()
        rec = self.registry.get(key)
        if rec is None:
            return VerifierResult(self.verifier_id, VerifierKind.AUTHORITATIVE_LOOKUP,
                                  VerifierOutcome.INCONCLUSIVE, is_llm=False,
                                  detail=f"no registry entry for {key!r}")
        integrity = (
            hashlib.sha256(rec.value.encode()).hexdigest() == rec.content_hash
            and registry_record_is_trusted(rec)
        )
        outcome = VerifierOutcome.PASS if rec.value == expected else VerifierOutcome.FAIL
        return VerifierResult(self.verifier_id, VerifierKind.AUTHORITATIVE_LOOKUP, outcome,
                              is_llm=False, source_authority=rec.authority_tier,
                              source_integrity_verified=integrity, formalization_checked=True,
                              detail=f"{key}={rec.value!r} vs expected {expected!r}")


# --------------------------------------------------------------------------- #
# Illustrative LLM "verifier" - can assist, can NEVER ground Verified
# --------------------------------------------------------------------------- #
class LlmAssertVerifier:
    """An LLM asserting a claim is true. By the non-LLM rule, a PASS here never
    grounds `Verified`; the status engine routes it to the evidence ladder."""
    verifier_id = "llm-entailment"

    def applies_to(self, ex: ExtractedClaim) -> bool:
        return True

    def verify(self, ex: ExtractedClaim) -> VerifierResult:
        return VerifierResult(self.verifier_id, VerifierKind.LLM_ENTAILMENT,
                              VerifierOutcome.PASS, is_llm=True, formalization_checked=True,
                              detail="LLM asserts the claim is true (assistive only)")


_BUILTIN_VERIFIER_TYPES = (ArithmeticVerifier, RegistryVerifier, LlmAssertVerifier)


def verifier_is_registered(verifier: object) -> bool:
    """Return True only for exact, code-owned verifier implementations.

    Subclasses and protocol-shaped caller objects are intentionally excluded. A
    future extension verifier must enter through the typed capability registry,
    where its implementation and assurance ceiling can be allowlisted explicitly.
    """
    return type(verifier) in _BUILTIN_VERIFIER_TYPES
