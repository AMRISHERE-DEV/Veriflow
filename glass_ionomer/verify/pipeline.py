"""
The assurance spine, wired end to end:

    extract -> admit evidence -> run applicable verifiers -> decide status

This is the smallest path that can prove a claim is not merely model consensus
but has earned a defensible evidence status. Everything outside it (mode router,
blind review, human queue, impact index, source monitor) is deferred.

Stdlib only.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .admissibility import EntailmentFn, admit
from .extraction import ExtractedClaim, extract_claim
from .resolver import ResolverFn, resolve
from .status import DEFAULT_POLICY, StatusDecision, StatusPolicy, decide_status
from .verifiers import Verifier, verifier_is_registered


@dataclass(frozen=True)
class VerificationOutcome:
    decision: StatusDecision
    applied_verifiers: tuple
    binding_ok: bool
    inadmissible_leads: tuple = ()   # leads the resolver could not admit (fail-open visibility)
    rejected_verifiers: tuple = ()   # caller objects excluded by the code-owned verifier allowlist
    resolved_records: tuple = ()     # immutable evidence ledger input, including rejected records
    evidence_items: tuple = ()       # computed admissibility/provenance decisions


def verify_extracted(ex: ExtractedClaim,
                     evidence_records: Sequence,
                     verifiers: Sequence[Verifier],
                     *,
                     now: datetime,
                     policy: StatusPolicy = DEFAULT_POLICY,
                     max_age: timedelta = timedelta(days=3650),
                     entailment_fn: EntailmentFn | None = None,
                     resolver_fn: ResolverFn | None = None,
                     require_provenance: bool = False) -> VerificationOutcome:
    records, inadmissible = resolve(evidence_records, resolver_fn=resolver_fn)
    evidence = admit(
        records,
        now=now,
        max_age=max_age,
        entailment_fn=entailment_fn,
        require_provenance=require_provenance,
    )
    eligible = [v for v in verifiers if verifier_is_registered(v)]
    rejected = [f"{type(v).__module__}.{type(v).__qualname__}" for v in verifiers
                if not verifier_is_registered(v)]
    applied = [v for v in eligible if v.applies_to(ex)]
    results = [v.verify(ex) for v in applied]
    decision = decide_status(ex.claim, results, evidence, now=now, policy=policy)
    return VerificationOutcome(decision=decision,
                               applied_verifiers=tuple(v.verifier_id for v in applied),
                               binding_ok=ex.binding_ok,
                               inadmissible_leads=tuple(inadmissible),
                               rejected_verifiers=tuple(rejected),
                               resolved_records=tuple(records),
                               evidence_items=tuple(evidence))


def verify_text(text: str,
                evidence_records: Sequence,   # Lead | EvidenceRecord (resolved when resolver_fn given)
                verifiers: Sequence[Verifier],
                *,
                now: datetime,
                cid: str = "c",
                ttl: timedelta | None = None,
                policy: StatusPolicy = DEFAULT_POLICY,
                max_age: timedelta = timedelta(days=3650),
                entailment_fn: EntailmentFn | None = None,
                resolver_fn: ResolverFn | None = None,
                require_provenance: bool = False) -> VerificationOutcome:
    """Convenience: extract a claim from raw text, then verify it."""
    ex = extract_claim(text, cid=cid, ttl=ttl)
    return verify_extracted(ex, evidence_records, verifiers, now=now, policy=policy,
                            max_age=max_age, entailment_fn=entailment_fn,
                            resolver_fn=resolver_fn,
                            require_provenance=require_provenance)
