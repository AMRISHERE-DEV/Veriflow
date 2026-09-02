"""
Project Saturn Verify - Evidence Status Policy (day-one canonical artifact).

This module is the spine of the system. It turns verifier results and admissible
evidence into a single, defensible EVIDENCE STATUS, and keeps that status honest:

  * execution state ("could we even check?") is tracked separately from evidence
    status ("what does the evidence say?");
  * VERIFIED requires a NON-LLM definitive verifier (the non-LLM rule) AND a
    checked claim-binding (the formalization rule);
  * each claim has a verifiability ceiling it cannot exceed;
  * positive statuses expire (TTL) so they cannot silently rot;
  * the status of a dependent claim is composed by its weakest admissible link.

The human-readable contract is docs/STATUS_POLICY.md. The tests in
tests/test_status_policy.py prove each status is reachable only via its predicate.

Stdlib only. No third-party dependencies.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum


def _finite_unit(value: float, name: str) -> float:
    """Validate a value is a finite float in [0, 1]; raise otherwise.
    inf/NaN must NEVER pass a threshold or silently drop evidence (finding #5)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if not (0.0 <= value <= 1.0):
        raise ValueError(f"{name} must be within [0, 1], got {value!r}")
    return value


# --------------------------------------------------------------------------- #
# Axes
# --------------------------------------------------------------------------- #
class VerifiabilityClass(Enum):
    """Assigned at extraction. Determines the status ceiling (typed eligibility)."""
    MECHANICAL = "mechanical"      # decidable by a non-LLM mechanism -> eligible for VERIFIED
    EMPIRICAL = "empirical"        # supportable by sources, not mechanically decidable -> max CORROBORATED
    INTERPRETIVE = "interpretive"  # qualitative / definitional / contested-by-nature -> max SUPPORTED


class EvidenceStatus(str, Enum):
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    SUPPORTED = "supported"
    CONTESTED = "contested"
    UNVERIFIED = "unverified"
    REFUTED = "refuted"            # refuted in scope by a definitive applicable verifier
    EXPIRED = "expired"            # was positive; TTL lapsed; must be re-verified


class ExecutionState(Enum):
    OK = "ok"
    NO_EVIDENCE = "no_evidence"
    ERROR = "error"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SKIPPED = "skipped"


class Direction(Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    NEUTRAL = "neutral"


class AuthorityTier(Enum):
    AUTHORITATIVE = "authoritative"  # official registry / standard / primary signed source
    PEER_REVIEWED = "peer_reviewed"
    PREPRINT = "preprint"
    DATASET = "dataset"
    WEB = "web"
    UNKNOWN = "unknown"


class VerifierKind(Enum):
    # Intrinsically definitive: authority is the mechanism itself (no external source).
    SYMBOLIC = "symbolic"            # CAS / SymPy
    FORMAL_PROOF = "formal_proof"    # Z3 / theorem prover
    CODE_TEST = "code_test"          # executable test
    DETERMINISTIC = "deterministic"  # generic deterministic calculation
    # Sourced definitive: authority comes from an external source -> needs tier + integrity.
    AUTHORITATIVE_LOOKUP = "authoritative_lookup"  # official registry / API field match
    SIGNED_DOCUMENT = "signed_document"
    RULES_ENGINE = "rules_engine"    # deterministic rules over authoritative structured data
    # LLM-mediated: may assist, may NOT ground VERIFIED.
    LLM_ENTAILMENT = "llm_entailment"
    LLM_CRITIC = "llm_critic"


INTRINSIC_DEFINITIVE = frozenset({
    VerifierKind.SYMBOLIC, VerifierKind.FORMAL_PROOF,
    VerifierKind.CODE_TEST, VerifierKind.DETERMINISTIC,
})
SOURCED_DEFINITIVE = frozenset({
    VerifierKind.AUTHORITATIVE_LOOKUP, VerifierKind.SIGNED_DOCUMENT,
    VerifierKind.RULES_ENGINE,
})
DEFINITIVE_KINDS = INTRINSIC_DEFINITIVE | SOURCED_DEFINITIVE


class VerifierOutcome(Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
    TIMEOUT = "timeout"


class Entailment(Enum):
    ENTAILS = "entails"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"


# --------------------------------------------------------------------------- #
# Ladder + ceilings
# --------------------------------------------------------------------------- #
# Positive-assurance ordering, used for ceilings and composition. REFUTED is the
# floor; EXPIRED/UNVERIFIED share the lowest non-refuted rung.
_LADDER = {
    EvidenceStatus.VERIFIED: 5,
    EvidenceStatus.CORROBORATED: 4,
    EvidenceStatus.SUPPORTED: 3,
    EvidenceStatus.CONTESTED: 2,
    EvidenceStatus.UNVERIFIED: 1,
    EvidenceStatus.EXPIRED: 1,
    EvidenceStatus.REFUTED: 0,
}
POSITIVE = (EvidenceStatus.VERIFIED, EvidenceStatus.CORROBORATED, EvidenceStatus.SUPPORTED)

CEILING = {
    VerifiabilityClass.MECHANICAL: EvidenceStatus.VERIFIED,
    VerifiabilityClass.EMPIRICAL: EvidenceStatus.CORROBORATED,
    VerifiabilityClass.INTERPRETIVE: EvidenceStatus.SUPPORTED,
}


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    verifiability_class: VerifiabilityClass
    polarity: bool = True            # entailment of evidence is evaluated wrt this polarity
    scope: str = ""                  # conditions under which the claim is asserted
    ttl: timedelta | None = None  # how long a positive status stays valid
    depends_on: tuple = ()           # ids of premise claims (conjunctive)


@dataclass(frozen=True)
class EvidenceItem:
    lineage_id: str                  # distinct source lineage (for independence counting)
    authority_tier: AuthorityTier
    entailment: Entailment           # wrt the claim's polarity/scope
    integrity_verified: bool         # content hash matched a trusted record
    fresh: bool                      # passes the freshness rule
    applicable: bool = True          # in-scope for the claim
    strength: float = 1.0            # admissibility-weighted strength in [0, 1]
    source_bound: bool = False       # resolver-path provenance LABEL (observability only; no gate; forgeable)
    independence_key: str | None = None
    quote_bound: bool = True
    model_derived_stance: bool = False
    # True only when the source identity/content bundle carries a system-issued,
    # subject-bound provenance proof.  A matching self-hash is integrity, not
    # provenance, and is insufficient for CORROBORATED.
    provenance_verified: bool = False

    def __post_init__(self):
        # strength gates support/contradiction; inf must not pass thresholds and
        # NaN must not silently drop evidence. Validate-and-raise (finding #5).
        _finite_unit(self.strength, "EvidenceItem.strength")


@dataclass(frozen=True)
class VerifierResult:
    verifier_id: str
    kind: VerifierKind
    outcome: VerifierOutcome
    is_llm: bool
    source_authority: AuthorityTier = AuthorityTier.UNKNOWN
    source_integrity_verified: bool = False
    formalization_checked: bool = False  # NL->formal claim-binding independently checked
    applicable: bool = True              # the verifier's scope matches the claim's scope
    detail: str = ""


@dataclass(frozen=True)
class StatusPolicy:
    corroboration_min_lineages: int = 2
    support_min_strength: float = 0.3
    contradiction_min_strength: float = 0.5
    min_authority_for_verified: tuple = (AuthorityTier.AUTHORITATIVE,)

    def __post_init__(self):
        # Validate-and-raise on a policy that would make a status reachable without
        # evidence (finding #5). corroboration_min_lineages must be >= 1 so a claim
        # can never become CORROBORATED with ZERO supporting lineages.
        if not isinstance(self.corroboration_min_lineages, int) \
                or isinstance(self.corroboration_min_lineages, bool):
            raise ValueError("corroboration_min_lineages must be an int")
        if self.corroboration_min_lineages < 1:
            raise ValueError(
                "corroboration_min_lineages must be >= 1 "
                f"(got {self.corroboration_min_lineages}); 0 would corroborate with no support")
        _finite_unit(self.support_min_strength, "support_min_strength")
        _finite_unit(self.contradiction_min_strength, "contradiction_min_strength")
        if not self.min_authority_for_verified:
            raise ValueError("min_authority_for_verified must be non-empty")
        for tier in self.min_authority_for_verified:
            if not isinstance(tier, AuthorityTier):
                raise ValueError(
                    f"min_authority_for_verified must contain AuthorityTier values, got {tier!r}")


DEFAULT_POLICY = StatusPolicy()


@dataclass(frozen=True)
class StatusDecision:
    claim_id: str
    status: EvidenceStatus
    execution_state: ExecutionState
    verifiability_class: VerifiabilityClass
    direction: Direction
    contested: bool
    support_lineages: int
    has_definitive_pass: bool
    reasons: tuple
    decided_at: datetime
    expires_at: datetime | None = None
    # True means the positive evidence ladder rests entirely on model-assigned
    # entailment. Synthesis must not present that decision as a settled fact.
    model_derived_support_only: bool = False
    provenance_verified_support: bool = False


# --------------------------------------------------------------------------- #
# Core decision
# --------------------------------------------------------------------------- #
def decide_status(
    claim: Claim,
    verifier_results: Sequence[VerifierResult],
    evidence: Sequence[EvidenceItem],
    *,
    now: datetime,
    policy: StatusPolicy = DEFAULT_POLICY,
    execution_state: ExecutionState = ExecutionState.OK,
) -> StatusDecision:
    """Assign an evidence status to a single claim. See docs/STATUS_POLICY.md."""
    reasons: list[str] = []

    # 1) Admissibility filter. Inadmissible evidence is dropped, not silently used.
    admissible = [e for e in evidence if e.applicable and e.integrity_verified and e.fresh]
    dropped = len(evidence) - len(admissible)
    if dropped:
        reasons.append(f"{dropped} evidence item(s) dropped as inadmissible (integrity/freshness/scope)")

    # 2) Execution gate. "Could not check" is never an evidence conclusion.
    if execution_state in (
        ExecutionState.ERROR, ExecutionState.TIMEOUT,
        ExecutionState.BUDGET_EXHAUSTED, ExecutionState.SKIPPED,
    ):
        reasons.append(f"execution did not complete normally: {execution_state.value}")
        return _finalize(claim, EvidenceStatus.UNVERIFIED, execution_state,
                         Direction.NEUTRAL, False, 0, False, reasons, now, policy)

    if not verifier_results and not evidence:
        reasons.append("no verifier ran and no evidence retrieved")
        return _finalize(claim, EvidenceStatus.UNVERIFIED, ExecutionState.NO_EVIDENCE,
                         Direction.NEUTRAL, False, 0, False, reasons, now, policy)

    # 3) Evidence support / contradiction over the admissible set.
    supports = [e for e in admissible
                if e.entailment is Entailment.ENTAILS and e.strength >= policy.support_min_strength and e.quote_bound]
    contradicts = [e for e in admissible
                   if e.entailment is Entailment.CONTRADICTS and e.strength >= policy.contradiction_min_strength and e.quote_bound]
    support_lineages = len({(('prov', e.independence_key) if e.independence_key else ('lineage', e.lineage_id)) for e in supports})
    trusted_supports = [e for e in supports if e.provenance_verified]
    trusted_support_lineages = len({
        (('prov', e.independence_key) if e.independence_key else ('lineage', e.lineage_id))
        for e in trusted_supports
    })
    credible_contradiction = bool(contradicts)
    model_derived_support_only = bool(supports) and all(
        e.model_derived_stance for e in supports)

    # 4) Definitive (non-LLM) verifier signal - the only thing that can ground VERIFIED.
    def is_definitive(v: VerifierResult) -> bool:
        if v.is_llm or not v.applicable:
            return False
        if v.kind in INTRINSIC_DEFINITIVE:
            return True
        if v.kind in SOURCED_DEFINITIVE:
            return v.source_integrity_verified and v.source_authority in policy.min_authority_for_verified
        return False

    definitive_pass = [v for v in verifier_results
                       if is_definitive(v) and v.outcome is VerifierOutcome.PASS
                       and v.formalization_checked]
    definitive_fail = [v for v in verifier_results
                       if is_definitive(v) and v.outcome is VerifierOutcome.FAIL
                       and v.formalization_checked]
    unbound_definitive_pass = [v for v in verifier_results
                               if is_definitive(v) and v.outcome is VerifierOutcome.PASS
                               and not v.formalization_checked]
    unbound_definitive_fail = [v for v in verifier_results
                               if is_definitive(v) and v.outcome is VerifierOutcome.FAIL
                               and not v.formalization_checked]

    # The non-LLM rule, made explicit and observable.
    llm_pass = [v for v in verifier_results if v.is_llm and v.outcome is VerifierOutcome.PASS]
    if llm_pass and not definitive_pass:
        reasons.append("LLM verifier(s) passed but no non-LLM definitive verifier "
                       "- VERIFIED unreachable (non-LLM rule)")

    contested = bool(supports) and credible_contradiction

    # 5) Raw status predicate (ceiling applied afterwards).
    if definitive_fail:
        candidate = EvidenceStatus.REFUTED
        direction = Direction.REFUTES
        reasons.append(f"definitive applicable verifier refutes claim in scope ({definitive_fail[0].verifier_id})")
    elif definitive_pass:
        if credible_contradiction:
            # A non-LLM definitive pass that coexists with a credible contradiction
            # is NOT released as VERIFIED: it is CONTESTED for human review. This
            # mirrors the typed spine (commit_status) so the two layers agree, and
            # keeps the gate conservative - a disputed claim never ships VERIFIED.
            candidate = EvidenceStatus.CONTESTED
            direction = Direction.NEUTRAL
            contested = True
            reasons.append(f"non-LLM definitive verifier passed ({definitive_pass[0].verifier_id}) "
                           "but a credible source disputes it - CONTESTED for human review")
        else:
            candidate = EvidenceStatus.VERIFIED
            direction = Direction.SUPPORTS
            reasons.append(f"non-LLM definitive verifier passed with checked claim-binding "
                           f"({definitive_pass[0].verifier_id})")
    else:
        if unbound_definitive_pass:
            reasons.append("definitive verifier passed but claim-binding (formalization) unchecked "
                           "- not eligible for VERIFIED")
        if trusted_support_lineages >= policy.corroboration_min_lineages and not credible_contradiction:
            candidate, direction = EvidenceStatus.CORROBORATED, Direction.SUPPORTS
            reasons.append(
                f"{trusted_support_lineages} independent provenance-verified lineages support the claim")
        elif support_lineages >= 1 and not credible_contradiction:
            candidate, direction = EvidenceStatus.SUPPORTED, Direction.SUPPORTS
            if trusted_support_lineages < policy.corroboration_min_lineages:
                reasons.append(
                    "support exists but provenance-verified corroboration threshold is unmet")
            else:  # pragma: no cover - guarded by the preceding branch
                reasons.append("one admissible lineage supports the claim; corroboration threshold unmet")
        elif supports and contradicts:
            candidate, direction = EvidenceStatus.CONTESTED, Direction.NEUTRAL
            reasons.append("credible support and contradiction coexist")
        elif contradicts and not supports:
            candidate, direction = EvidenceStatus.UNVERIFIED, Direction.REFUTES
            reasons.append("admissible contradiction present, no support, no definitive refuter")
        elif unbound_definitive_fail:
            candidate, direction = EvidenceStatus.UNVERIFIED, Direction.REFUTES
            reasons.append("definitive verifier failed but claim-binding (formalization) unchecked "
                           "- not eligible for REFUTED")
        else:
            candidate, direction = EvidenceStatus.UNVERIFIED, Direction.NEUTRAL
            reasons.append("insufficient admissible evidence")

    # 6) Ceiling cap by verifiability class.
    final = _apply_ceiling(candidate, claim.verifiability_class, reasons)

    return _finalize(claim, final, ExecutionState.OK, direction, contested,
                     support_lineages, bool(definitive_pass), reasons, now, policy,
                     model_derived_support_only=model_derived_support_only,
                     provenance_verified_support=bool(trusted_supports))


def _apply_ceiling(candidate: EvidenceStatus, vclass: VerifiabilityClass,
                   reasons: list) -> EvidenceStatus:
    ceiling = CEILING[vclass]
    if candidate in POSITIVE and _LADDER[candidate] > _LADDER[ceiling]:
        reasons.append(f"capped {candidate.value} -> {ceiling.value} by verifiability class {vclass.value}")
        return ceiling
    return candidate


def _finalize(claim, status, exec_state, direction, contested, support_lineages,
              has_def_pass, reasons, now, policy,
              model_derived_support_only=False,
              provenance_verified_support=False) -> StatusDecision:
    expires_at = None
    if status in POSITIVE and claim.ttl is not None:
        expires_at = now + claim.ttl
    return StatusDecision(
        claim_id=claim.id,
        status=status,
        execution_state=exec_state,
        verifiability_class=claim.verifiability_class,
        direction=direction,
        contested=contested,
        support_lineages=support_lineages,
        has_definitive_pass=has_def_pass,
        reasons=tuple(reasons),
        decided_at=now,
        expires_at=expires_at,
        model_derived_support_only=model_derived_support_only,
        provenance_verified_support=provenance_verified_support,
    )


# --------------------------------------------------------------------------- #
# TTL / staleness
# --------------------------------------------------------------------------- #
def is_expired(decision: StatusDecision, now: datetime) -> bool:
    return (decision.expires_at is not None
            and now >= decision.expires_at
            and decision.status in POSITIVE)


def refresh_for_staleness(decision: StatusDecision, now: datetime) -> StatusDecision:
    """Auto-demote a positive status whose TTL has lapsed. Status is maintained,
    not a snapshot: a Verified claim cannot silently rot."""
    if is_expired(decision, now):
        return replace(
            decision,
            status=EvidenceStatus.EXPIRED,
            direction=Direction.NEUTRAL,
            reasons=(*decision.reasons, "status TTL lapsed; re-verification required"),
        )
    return decision


# --------------------------------------------------------------------------- #
# Composition of dependent claims (weakest admissible link)
# --------------------------------------------------------------------------- #
def compose(conclusion: StatusDecision,
            premises: Sequence[StatusDecision],
            now: datetime) -> StatusDecision:
    """A conclusion can be no stronger than its weakest admissible premise.
    Conjunctive semantics: any refuted premise breaks the conclusion; any expired
    premise forces re-verification; otherwise cap to the weakest positive rung.

    Composition is a REUSE boundary, so TTL is enforced here rather than trusted to the caller:
    the conclusion and every premise are passed through refresh_for_staleness(now) first. Without
    this, a premise whose TTL had lapsed but whose stored status still read VERIFIED would lend
    that strength to a new conclusion - a silently rotted certification."""
    conclusion = refresh_for_staleness(conclusion, now)
    if not premises:
        return conclusion
    premises = [refresh_for_staleness(p, now) for p in premises]

    # The weakest link bounds the DEADLINE as well as the status: a conclusion may not outlive the
    # premise holding it up, or reusing it after that premise lapses would return a live status
    # from stale support. Inherit the earliest applicable deadline.
    _deadlines = [d.expires_at for d in (conclusion, *premises) if d.expires_at is not None]
    earliest_expiry = min(_deadlines) if _deadlines else None

    reasons = list(conclusion.reasons)
    contested = conclusion.contested or any(p.contested for p in premises)
    model_derived_support_only = (
        conclusion.model_derived_support_only
        or any(p.model_derived_support_only for p in premises)
    )

    refuted = [p for p in premises if p.status is EvidenceStatus.REFUTED]
    if refuted:
        reasons.append(f"premise {refuted[0].claim_id} is REFUTED; conjunction broken")
        return replace(conclusion, status=EvidenceStatus.REFUTED,
                       direction=Direction.REFUTES, contested=contested,
                       model_derived_support_only=model_derived_support_only,
                       reasons=tuple(reasons), decided_at=now, expires_at=earliest_expiry)

    expired = [p for p in premises if p.status is EvidenceStatus.EXPIRED]
    if expired:
        if conclusion.status is EvidenceStatus.REFUTED:
            reasons.append(f"premise {expired[0].claim_id} is EXPIRED, but conclusion is already REFUTED")
            return replace(
                conclusion,
                contested=contested,
                model_derived_support_only=model_derived_support_only,
                reasons=tuple(reasons),
                decided_at=now, expires_at=earliest_expiry,
            )
        reasons.append(f"premise {expired[0].claim_id} is EXPIRED; conclusion must be re-verified")
        return replace(conclusion, status=EvidenceStatus.EXPIRED,
                       direction=Direction.NEUTRAL, contested=contested,
                       model_derived_support_only=model_derived_support_only,
                       reasons=tuple(reasons), decided_at=now, expires_at=earliest_expiry)

    weakest = min([conclusion, *premises], key=lambda d: _LADDER[d.status])
    if _LADDER[weakest.status] < _LADDER[conclusion.status]:
        reasons.append(f"downgraded {conclusion.status.value} -> {weakest.status.value} "
                       f"by weakest premise {weakest.claim_id}")
        return replace(conclusion, status=weakest.status, contested=contested,
                       model_derived_support_only=model_derived_support_only,
                       reasons=tuple(reasons), decided_at=now, expires_at=earliest_expiry)

    if contested != conclusion.contested:
        reasons.append("contested flag inherited from a premise")
    return replace(
        conclusion,
        contested=contested,
        model_derived_support_only=model_derived_support_only,
        reasons=tuple(reasons),
        decided_at=now, expires_at=earliest_expiry,
    )
