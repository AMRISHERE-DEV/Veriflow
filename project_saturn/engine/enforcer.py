"""The Enforcer - PXB-derived, DETERMINISTIC.

Maps a certified status (+ the Reasoner's advisories) to a release decision and a
signed, replayable receipt. The Enforcer is a pure function of its inputs; it mints
no status of its own.

Invariant (2) - "agreement is never evidence" - is enforced structurally: the
decision starts from the kernel-certified status, and advisories may only move it
UP the restrictiveness ladder (allow -> notice -> clarify -> escalate -> refuse).
There is no code path by which any advisory (including an LLM "this is true")
relaxes a decision.

Defense-in-depth for invariant (1) - "no false VERIFIED": even a status of VERIFIED
is only released (ALLOW) when the certification carries a real non-LLM definitive
verifier. A VERIFIED that is not so backed escalates instead of releasing.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import hmac

from project_saturn.verify.status import EvidenceStatus

from .contracts import (
    DISCLAIMER,
    ENGINE_VERSION,
    RESTRICTIVENESS,
    Certification,
    ProposedClaim,
    Receipt,
    ReleaseDecision,
    Severity,
    canonical_hash,
    legacy_canonical_hash,
    sha256_text,
)

# Certified status -> (base release decision, base risk tier).
STATUS_DECISION = {
    EvidenceStatus.VERIFIED: (ReleaseDecision.ALLOW, 0),
    EvidenceStatus.CORROBORATED: (ReleaseDecision.ALLOW_WITH_NOTICE, 1),
    # One admissible lineage is useful context, but not enough to release a
    # professional conclusion. More evidence or review is required.
    EvidenceStatus.SUPPORTED: (ReleaseDecision.REQUIRE_CLARIFICATION, 3),
    EvidenceStatus.CONTESTED: (ReleaseDecision.ESCALATE, 3),
    EvidenceStatus.UNVERIFIED: (ReleaseDecision.REQUIRE_CLARIFICATION, 3),
    EvidenceStatus.EXPIRED: (ReleaseDecision.REQUIRE_CLARIFICATION, 3),
    EvidenceStatus.REFUTED: (ReleaseDecision.REFUSE, 5),
}


def _advisory_floor(severity: Severity) -> tuple[ReleaseDecision | None, int]:
    """The minimum restrictiveness (and tier bump) an advisory imposes."""
    if severity is Severity.BLOCK:
        return ReleaseDecision.REFUSE, 5
    if severity is Severity.CAUTION:
        return ReleaseDecision.ALLOW_WITH_NOTICE, 1
    return None, 0  # INFO: observability only


def _more_restrictive(current: ReleaseDecision, floor: ReleaseDecision) -> ReleaseDecision:
    """Return the more restrictive of two decisions; never relaxes `current`."""
    return floor if RESTRICTIVENESS[floor] > RESTRICTIVENESS[current] else current


def _key_bytes(key: str | bytes) -> bytes:
    raw = key.encode("utf-8") if isinstance(key, str) else key
    if not raw:
        raise ValueError("signing_key must not be empty")
    return raw


def _content(proposed: ProposedClaim, cert: Certification,
             decision: ReleaseDecision, tier: int) -> dict:
    """Canonical receipt content. The ONLY thing hashed/signed; same inputs -> same hash."""
    return {
        "engine_version": ENGINE_VERSION,
        "claim_id": proposed.claim_id,
        "raw_claim_sha256": sha256_text(proposed.text),
        "verification_plan": proposed.verification_plan,
        "cert_status": cert.status.value,
        "cert_source": cert.source,
        "cert_definitive_nonllm": bool(cert.definitive_nonllm),
        "cert_record_hash": cert.record_hash,
        "advisories": [[a.name, a.severity.value] for a in proposed.advisories],
        "release_decision": decision.value,
        "risk_tier": tier,
        "disclaimer": DISCLAIMER,
    }


def _build_receipt(proposed, cert, decision, tier, signing_key) -> Receipt:
    content = _content(proposed, cert, decision, tier)
    rhash = canonical_hash(content)
    signature = None
    if signing_key is not None:
        signature = hmac.new(_key_bytes(signing_key), rhash.encode("utf-8"), hashlib.sha256).hexdigest()
    return Receipt(
        engine_version=ENGINE_VERSION,
        claim_id=proposed.claim_id,
        raw_claim_sha256=content["raw_claim_sha256"],
        verification_plan=proposed.verification_plan,
        cert_status=cert.status.value,
        cert_source=cert.source,
        cert_definitive_nonllm=bool(cert.definitive_nonllm),
        cert_record_hash=cert.record_hash,
        advisories=tuple((a.name, a.severity.value) for a in proposed.advisories),
        release_decision=decision.value,
        risk_tier=tier,
        disclaimer=DISCLAIMER,
        receipt_hash=rhash,
        signature=signature,
    )


def enforce(
    proposed: ProposedClaim,
    certification: Certification,
    *,
    signing_key: str | bytes | None = None,
) -> tuple[ReleaseDecision, int, Receipt, tuple]:
    """Deterministically map (certification, advisories) -> (decision, tier, receipt, reasons)."""
    reasons: list = []
    status = certification.status
    decision, tier = STATUS_DECISION.get(status, (ReleaseDecision.REQUIRE_CLARIFICATION, 3))
    reasons.append(f"certified status {status.value} -> {decision.value} (tier {tier})")

    # Defense-in-depth (invariant 1): a full release requires a real non-LLM verifier.
    if decision is ReleaseDecision.ALLOW and not certification.definitive_nonllm:
        decision, tier = ReleaseDecision.ESCALATE, 4
        reasons.append("status is releasable but no non-LLM definitive verifier backs it "
                       "- escalating instead of releasing (defense in depth)")

    # Invariant 2, release-layer floor: support whose every stance is model-assigned
    # is context, never a releasable conclusion.
    if (getattr(certification, "model_derived_support_only", False)
            and RESTRICTIVENESS[decision] < RESTRICTIVENESS[ReleaseDecision.REQUIRE_CLARIFICATION]):
        decision = ReleaseDecision.REQUIRE_CLARIFICATION
        tier = max(tier, 3)
        reasons.append("all supporting evidence stances are model-derived "
                       "- floored below release")

    # Advisories can only raise restrictiveness (invariant 2).
    for adv in proposed.advisories:
        floor, atier = _advisory_floor(adv.severity)
        if floor is not None:
            nxt = _more_restrictive(decision, floor)
            if nxt is not decision:
                reasons.append(f"advisory {adv.name!r} ({adv.severity.value}) -> {nxt.value}")
                decision = nxt
        tier = max(tier, atier)

    receipt = _build_receipt(proposed, certification, decision, tier, signing_key)
    return decision, tier, receipt, tuple(reasons)


def verify_receipt(receipt: Receipt, *, signing_key: str | bytes | None = None,
                   require_signature: bool = False) -> bool:
    """Re-derive the receipt hash (and HMAC, if a key is given) from the stored fields.

    Returns True iff the receipt is internally consistent, and - when a `signing_key` is given -
    carries a valid HMAC. WITHOUT a key, this proves only INTERNAL CONSISTENCY, not tamper-evidence:
    an attacker can edit a field and recompute `receipt_hash`, so an UNSIGNED receipt is not
    trustworthy for audit. For real audit pass `require_signature=True` (and a `signing_key`): an
    unsigned receipt, or one that cannot be checked against a key, is then rejected."""
    try:
        content = {
            "engine_version": receipt.engine_version,
            "claim_id": receipt.claim_id,
            "raw_claim_sha256": receipt.raw_claim_sha256,
            "verification_plan": receipt.verification_plan,
            "cert_status": receipt.cert_status,
            "cert_source": receipt.cert_source,
            "cert_definitive_nonllm": bool(receipt.cert_definitive_nonllm),
            "cert_record_hash": receipt.cert_record_hash,
            "advisories": [[name, sev] for (name, sev) in receipt.advisories],
            "release_decision": receipt.release_decision,
            "risk_tier": receipt.risk_tier,
            "disclaimer": receipt.disclaimer,
        }
    except (AttributeError, TypeError, ValueError):
        return False
    hash_fn = legacy_canonical_hash if receipt.engine_version == "project_saturn.engine.v1" else canonical_hash
    if hash_fn(content) != receipt.receipt_hash:
        return False
    if signing_key is not None:
        if not receipt.signature:
            return False
        expected = hmac.new(_key_bytes(signing_key), receipt.receipt_hash.encode("utf-8"),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, receipt.signature)
    # Without a key, an internally consistent receipt is forgeable and cannot
    # satisfy audit mode's signature requirement.
    return not require_signature
