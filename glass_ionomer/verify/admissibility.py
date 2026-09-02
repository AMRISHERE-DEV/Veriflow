"""
Admissibility: turn raw retrieved records into admissible EvidenceItems.

This is where the integrity and freshness gates actually run (deterministically,
here): integrity = content hash matches a trusted record; freshness = published
within max_age of `now`. Authority tier is carried through. Entailment (does this
passage support/contradict the claim, polarity-aware) is the genuinely
LLM-assisted signal; it is supplied on the record by default, with a seam for a
pluggable entailment classifier.

Inadmissible records are NOT dropped silently into "support" - they are emitted
as EvidenceItems with the failing gate flagged, and the status engine excludes
them (and records that it did).

Stdlib only.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .status import AuthorityTier, Entailment, EvidenceItem
from .trust import SourceProof, source_proof_is_trusted


# Whole-token quote binder for quote-binding (build #4). It is a
# necessary-not-sufficient anti-fabrication guard: it proves that the quoted span exists
# in the source. Claim relevance is checked separately by deliberate.bind_entailment;
# neither check proves the model assigned the correct semantic polarity.
def _norm(s): return re.sub(r'\s+', ' ', s.strip().lower())


def _span_bound(quote: str, content: str) -> bool:
    """True only when a normalized quote is a whole-token span in content."""
    if not quote:
        return False
    return re.search(
        rf"(?<![a-z0-9]){re.escape(quote)}(?![a-z0-9])", content,
    ) is not None


@dataclass(frozen=True)
class EvidenceRecord:
    lineage_id: str
    content: str
    authority_tier: AuthorityTier
    published: datetime
    trusted_hash: str | None   # known-good sha256 hex; None => integrity unverifiable
    stance: Entailment            # polarity-aware stance toward the claim
    applicable: bool = True
    strength: float = 1.0
    resolved_by: str | None = None   # resolver_id (observability label; NOT forgery-proof; no gate/status power); None => legacy/self-hash
    resolved_identity: str | None = None
    quote: str = ''
    model_derived_stance: bool = False
    source_proof: SourceProof | None = None

    @staticmethod
    def of(lineage_id: str, content: str, *, published: datetime,
           authority_tier: AuthorityTier = AuthorityTier.PEER_REVIEWED,
           stance: Entailment = Entailment.ENTAILS, applicable: bool = True,
           strength: float = 1.0, integrity: bool = True,
           quote: str = "") -> EvidenceRecord:
        """Build a record; integrity=True stamps the matching trusted hash."""
        h = hashlib.sha256(content.encode()).hexdigest() if integrity else "deadbeef"
        return EvidenceRecord(lineage_id=lineage_id, content=content,
                              authority_tier=authority_tier, published=published,
                              trusted_hash=h, stance=stance, applicable=applicable,
                              strength=strength, quote=quote)


EntailmentFn = Callable[[str], Entailment]


def evidence_source_subject(record: EvidenceRecord) -> dict:
    """Canonical, serialization-safe subject covered by a source proof."""
    return {
        "lineage_id": record.lineage_id,
        "content_sha256": hashlib.sha256(record.content.encode()).hexdigest(),
        "trusted_hash": record.trusted_hash or "",
        "authority_tier": record.authority_tier.value,
        "published": record.published.isoformat(),
        "resolved_by": record.resolved_by or "",
        "resolved_identity": record.resolved_identity or "",
    }


def admit(records: Sequence[EvidenceRecord], *, now: datetime,
          max_age: timedelta = timedelta(days=3650),
          entailment_fn: EntailmentFn | None = None,
          require_provenance: bool = False) -> list:
    """Compute integrity + freshness for each record and return EvidenceItems."""
    items = []
    for r in records:
        integrity = (r.trusted_hash is not None
                     and hashlib.sha256(r.content.encode()).hexdigest() == r.trusted_hash)
        provenance = source_proof_is_trusted(
            r.source_proof, subject=evidence_source_subject(r))
        # Freshness requires the record to be published in the PAST and within
        # max_age (finding #6). A future-dated record is not fresh: absence/forgery
        # of a date must never manufacture recency, and `now - published <= max_age`
        # alone is True for any future date.
        age = now - r.published
        fresh = (r.published <= now) and (age <= max_age)
        stance = entailment_fn(r.content) if entailment_fn is not None else r.stance
        nq, nc = _norm(r.quote), _norm(r.content)
        quote_bound = (nq == '') or _span_bound(nq, nc)
        if not quote_bound and stance is not Entailment.NEUTRAL:
            stance = Entailment.NEUTRAL    # fabricated/non-matching quote demotes ONLY (never invert)
        # Slice 1: a MODEL-derived (entailment_fn) stance on RESOLVER-sourced evidence
        # counts only when carried by a NON-EMPTY quote that binds to the content.
        # Quote-binding is NECESSARY, not sufficient: entailment stays the model's judgment.
        if (r.resolved_by is not None and entailment_fn is not None
                and stance is not Entailment.NEUTRAL
                and not (nq != '' and _span_bound(nq, nc))):
            stance = Entailment.NEUTRAL    # demote-only; resolver-sourced model entailment needs a bound span
        items.append(EvidenceItem(
            lineage_id=r.lineage_id,
            authority_tier=r.authority_tier,
            entailment=stance,
            integrity_verified=integrity,
            fresh=fresh,
            applicable=(r.applicable and (provenance or not require_provenance)),
            strength=r.strength,
            source_bound=(r.resolved_by is not None),
            independence_key=r.resolved_identity,
            quote_bound=quote_bound,
            model_derived_stance=(
                stance is not Entailment.NEUTRAL
                and (r.model_derived_stance or entailment_fn is not None)
            ),
            provenance_verified=provenance,
        ))
    return items
