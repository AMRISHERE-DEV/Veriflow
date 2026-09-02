"""Trust primitives shared by the kernel and every lane.

A trusted proof/wrapper can be minted ONLY by a system-owned issuance point (the factories here),
never by the data/LLM plane and never by plain construction. Trust is carried by a module-private
sentinel *identity* that cannot be forged from serialized data - it does not survive
replace/deepcopy/pickle/JSON, so a trusted object can never be rebuilt from attacker-controlled
values. The sentinel is stamped as an UNDECLARED attribute (not a dataclass field), so it never
appears in `dataclasses.asdict()` / `json.dumps()` output (no serialization leak or crash).

This is a data-plane boundary, not a Python sandbox. Code already executing in this interpreter can
call the issuance functions and is therefore part of the trusted operator plane. Deployments must
run untrusted plugins, model-generated code, and tenant code out of process; see
`docs/DEPLOYMENT_TRUST_BOUNDARY.md`.

Pure stdlib; imports nothing from glass_ionomer (a true leaf - safe to import from any layer).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from glass_ionomer._canonical import CanonicalizationError, canonical_sha256

# Module-private identities. "Trusted" == carrying THIS exact object; unforgeable from data.
_BINDING_TRUST_TOKEN = object()
_COVERAGE_TRUST_TOKEN = object()
_DERIVATION_TRUST_TOKEN = object()
_REVIEW_TRUST_TOKEN = object()
_SOURCE_TRUST_TOKEN = object()
_STAMP = object()   # internal caller-token: only code holding it may stamp a trusted wrapper

# Binding methods strong enough to let a definitive verifier mint VERIFIED.
# "lexical" is EXCLUDED on purpose: a token/operator check cannot certify meaning.
TRUSTED_BINDING_METHODS = frozenset({"provenance", "semantic", "human"})


@dataclass(frozen=True)
class BindingProof:
    """Evidence that a structured claim faithfully encodes the natural-language claim.

    `method` records HOW the binding was established. ONLY trusted methods may unlock VERIFIED; a
    lexical/structural check is deliberately NOT trusted. The trust token is NOT a dataclass field,
    so it never appears in asdict()/json - it is stamped as an undeclared attribute by the factory."""
    method: str            # "lexical" | "provenance" | "semantic" | "human"
    ok: bool
    detail: str = ""
    proof_hash: str = ""
    issuer: str = ""
    subject_hash: str = ""


@dataclass(frozen=True)
class CoverageProof:
    """System attestation over one exact claim/query/evidence coverage bundle."""
    ok: bool
    subject_hash: str
    detail: str = ""
    issuer: str = ""


@dataclass(frozen=True)
class DerivationProof:
    """System attestation over one exact conclusion and its premise graph."""
    ok: bool
    subject_hash: str
    detail: str = ""
    issuer: str = ""


@dataclass(frozen=True)
class ReviewProof:
    """System attestation over an exact human/tool evidence-review decision."""
    ok: bool
    subject_hash: str
    detail: str = ""
    issuer: str = ""


@dataclass(frozen=True)
class SourceProof:
    """System attestation that evidence bytes came through an approved resolver.

    The subject is the exact source identity/content/metadata bundle.  The private
    token is deliberately not serializable, so a caller cannot manufacture source
    provenance by setting labels such as ``resolved_by`` or by copying a digest.
    """
    ok: bool
    subject_hash: str
    resolver_id: str
    detail: str = ""


def binding_subject_hash(subject: Any) -> str:
    """Canonical digest of the exact claim/formalization bundle a proof reviewed."""
    return canonical_sha256(subject)


def _safe_subject_hash(subject: Any) -> str:
    """Return no authority-bearing hash when a subject is malformed or unbounded."""
    try:
        return binding_subject_hash(subject)
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        return ""


def issue_trusted_binding_proof(method: str, ok: bool, detail: str = "",
                                 proof_hash: str = "", issuer: str = "system",
                                 *, subject: Any = None) -> BindingProof:
    """Operator-plane issuance point.

    A directly constructed or deserialized BindingProof is inert. Calling this function is a
    privileged integration action and must never be exposed to untrusted in-process code.

    Issuance never raises (a malformed subject must be rejected downstream as a structural
    verdict, not as an exception). An un-canonicalizable subject simply yields NO
    authority-bearing hash, and binding_proof_is_trusted then fails CLOSED on the empty
    subject_hash - so an unbindable proof is inert rather than a trusted bearer token.
    """
    subject_hash = _safe_subject_hash(subject) if subject is not None else ""
    proof = BindingProof(
        method=method,
        ok=ok,
        detail=detail,
        proof_hash=proof_hash,
        issuer=issuer,
        subject_hash=subject_hash,
    )
    object.__setattr__(proof, "_trust_token", _BINDING_TRUST_TOKEN)  # undeclared -> not in asdict()
    return proof


def binding_proof_is_trusted(proof: BindingProof | None, *, subject: Any) -> bool:
    """Validate issuance AND binding to the exact reviewed subject.

    `subject` is required (matching coverage/derivation/review/source_proof_is_trusted): an
    omitted-subject call previously validated issuance alone, which would accept an unbound proof.
    There is no None escape hatch either - an empty subject_hash on the proof, or a subject that
    cannot be canonicalized, fails CLOSED."""
    expected = _safe_subject_hash(subject)
    subject_matches = (
        bool(expected)
        and bool(getattr(proof, "subject_hash", ""))
        and getattr(proof, "subject_hash", "") == expected
    )
    return bool(
        proof is not None
        and getattr(proof, "ok", False)
        and getattr(proof, "method", None) in TRUSTED_BINDING_METHODS
        and getattr(proof, "_trust_token", None) is _BINDING_TRUST_TOKEN
        and subject_matches
    )


def issue_trusted_coverage_proof(*, subject: Any, ok: bool = True, detail: str = "",
                                 issuer: str = "system") -> CoverageProof:
    proof = CoverageProof(
        ok=ok, subject_hash=_safe_subject_hash(subject), detail=detail, issuer=issuer)
    object.__setattr__(proof, "_trust_token", _COVERAGE_TRUST_TOKEN)
    return proof


def coverage_proof_is_trusted(proof: CoverageProof | None, *, subject: Any) -> bool:
    return bool(
        proof is not None
        and getattr(proof, "ok", False)
        and getattr(proof, "_trust_token", None) is _COVERAGE_TRUST_TOKEN
        and bool(_safe_subject_hash(subject))
        and getattr(proof, "subject_hash", "") == _safe_subject_hash(subject)
    )


def issue_trusted_derivation_proof(*, subject: Any, ok: bool = True, detail: str = "",
                                   issuer: str = "system") -> DerivationProof:
    proof = DerivationProof(
        ok=ok, subject_hash=_safe_subject_hash(subject), detail=detail, issuer=issuer)
    object.__setattr__(proof, "_trust_token", _DERIVATION_TRUST_TOKEN)
    return proof


def derivation_proof_is_trusted(proof: DerivationProof | None, *, subject: Any) -> bool:
    return bool(
        proof is not None
        and getattr(proof, "ok", False)
        and getattr(proof, "_trust_token", None) is _DERIVATION_TRUST_TOKEN
        and bool(_safe_subject_hash(subject))
        and getattr(proof, "subject_hash", "") == _safe_subject_hash(subject)
    )


def issue_trusted_review_proof(*, subject: Any, ok: bool = True, detail: str = "",
                               issuer: str = "system") -> ReviewProof:
    proof = ReviewProof(
        ok=ok, subject_hash=_safe_subject_hash(subject), detail=detail, issuer=issuer)
    object.__setattr__(proof, "_trust_token", _REVIEW_TRUST_TOKEN)
    return proof


def review_proof_is_trusted(proof: ReviewProof | None, *, subject: Any) -> bool:
    return bool(
        proof is not None
        and getattr(proof, "ok", False)
        and getattr(proof, "_trust_token", None) is _REVIEW_TRUST_TOKEN
        and bool(_safe_subject_hash(subject))
        and getattr(proof, "subject_hash", "") == _safe_subject_hash(subject)
    )


def issue_trusted_source_proof(*, subject: Any, resolver_id: str,
                               ok: bool = True, detail: str = "") -> SourceProof:
    """Operator-plane issuance point for resolver-owned source bytes."""
    proof = SourceProof(
        ok=ok,
        subject_hash=_safe_subject_hash(subject),
        resolver_id=resolver_id,
        detail=detail,
    )
    object.__setattr__(proof, "_trust_token", _SOURCE_TRUST_TOKEN)
    return proof


def source_proof_is_trusted(proof: SourceProof | None, *, subject: Any) -> bool:
    return bool(
        proof is not None
        and getattr(proof, "ok", False)
        and getattr(proof, "_trust_token", None) is _SOURCE_TRUST_TOKEN
        and bool(_safe_subject_hash(subject))
        and getattr(proof, "subject_hash", "") == _safe_subject_hash(subject)
    )
