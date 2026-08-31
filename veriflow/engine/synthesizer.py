"""The Synthesizer - compose certified claims into a defensible CONCLUSION.

This is the first organ that makes VeriFlow more than a per-claim gate: it takes several
already-certified claims (EngineResults) and composes them into ONE conclusion whose
assurance can never exceed its weakest admissible premise, carrying a replayable support
graph + a signed receipt. It mints no belief of its own - synthesis is bookkeeping over
statuses the non-LLM core already decided.

It reuses the tested kernel `compose()` (verify/status.py): conjunction, weakest-link,
any REFUTED premise breaks the conclusion, any EXPIRED premise forces re-verification.

Guardrails carried (from the adversary review):
  * Nodes are recomputed from each premise's actual Certification, never caller snapshots;
    `graph_hash` is a canonical hash over the PREMISE record_hashes, so tampering a node
    changes the anchor (verify_conclusion re-derives and checks it).
  * A caller-supplied `derivation_checked` boolean is advisory only. Until a system-issued
    derivation proof is bound to the conclusion and premise hashes, synthesis cannot carry
    definitive status and the Enforcer escalates it.
  * Min-ceiling seed: the conclusion is seeded at its OWN verifiability ceiling, so a
    derived/INTERPRETIVE conclusion is capped at SUPPORTED even if all premises are VERIFIED
    (plain compose() would otherwise let it inherit a premise's higher rung).
  * Conjunction only. Disjunction is intentionally NOT implemented (it has no tested
    compose() dual and is the classic agreement-laundering vector).

Stdlib only.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from veriflow.verify.status import (
    CEILING,
    POSITIVE,
    Direction,
    EvidenceStatus,
    ExecutionState,
    StatusDecision,
    compose,
)
from veriflow.verify.trust import DerivationProof, derivation_proof_is_trusted

from .contracts import (
    AdvisorySignal,
    Certification,
    EngineResult,
    ProposedClaim,
    Receipt,
    ReleaseDecision,
    Severity,
    canonical_hash,
)
from .enforcer import enforce, verify_receipt


@dataclass(frozen=True)
class SupportNode:
    """One premise's contribution to a conclusion - copied from its real Certification."""
    claim_id: str
    status: str
    definitive_nonllm: bool
    record_hash: str


@dataclass(frozen=True)
class ConclusionResult:
    conclusion: ProposedClaim
    composed_status: str            # EvidenceStatus value (weakest-link, ceiling-capped)
    release_decision: ReleaseDecision
    risk_tier: int
    support_graph: tuple            # tuple[SupportNode, ...]
    graph_hash: str
    receipt: Receipt
    reasons: tuple


def _graph_hash(conclusion_id: str, nodes: Sequence[SupportNode]) -> str:
    """Anchor every support-node authority field, including the definitive bit."""
    return canonical_hash({
        "conclusion": conclusion_id,
        "premises": sorted(({
            "claim_id": node.claim_id,
            "status": node.status,
            "definitive_nonllm": node.definitive_nonllm,
            "record_hash": node.record_hash,
        } for node in nodes), key=lambda item: (item["claim_id"], item["record_hash"])),
    })


def _premise_decision(premise: EngineResult, now: datetime) -> StatusDecision:
    """Build a StatusDecision from a premise's REAL certification (not a snapshot)."""
    st = premise.certification.status
    direction = (Direction.SUPPORTS if st in POSITIVE
                 else Direction.REFUTES if st is EvidenceStatus.REFUTED
                 else Direction.NEUTRAL)
    return StatusDecision(
        claim_id=premise.proposed.claim_id,
        status=st,
        execution_state=ExecutionState.OK,
        verifiability_class=premise.proposed.verifiability_class,
        direction=direction,
        contested=(st is EvidenceStatus.CONTESTED),
        support_lineages=0,
        has_definitive_pass=premise.certification.definitive_nonllm,
        reasons=premise.certification.reasons,
        decided_at=now,
    )


def _subject_payload(value):
    """Convert typed payload objects into explicit, authority-free canonical data."""
    if dataclasses.is_dataclass(value):
        return {
            "__dataclass__": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": {
                field.name: _subject_payload(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }
    if isinstance(value, Enum):
        return {
            "__enum__": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _subject_payload(value.value),
        }
    if isinstance(value, dict):
        return {key: _subject_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_subject_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_subject_payload(item) for item in value)
    return value


def derivation_subject(conclusion: ProposedClaim, premises: Sequence[EngineResult]) -> dict:
    """Canonical conclusion and exact certified premises reviewed by a derivation proof."""
    premise_bundles = [{
        "claim_id": premise.proposed.claim_id,
        "text": premise.proposed.text,
        "verifiability_class": premise.proposed.verifiability_class.value,
        "verification_plan": premise.proposed.verification_plan,
        "payload": _subject_payload(premise.proposed.payload),
        "status": premise.certification.status.value,
        "definitive_nonllm": premise.certification.definitive_nonllm,
        "record_hash": premise.certification.record_hash,
    } for premise in premises if premise is not None]
    return {
        "conclusion": {
            "claim_id": conclusion.claim_id,
            "text": conclusion.text,
            "verifiability_class": conclusion.verifiability_class.value,
            "verification_plan": conclusion.verification_plan,
            "payload": _subject_payload(conclusion.payload),
        },
        "premises": sorted(
            premise_bundles,
            key=lambda item: (item["claim_id"], item["record_hash"], item["status"]),
        ),
    }


def synthesize(
    conclusion: ProposedClaim,
    premises: Sequence[EngineResult],
    *,
    derivation_checked: bool = False,
    derivation_proof: DerivationProof | None = None,
    now: datetime | None = None,
    signing_key: str | bytes | None = None,
) -> ConclusionResult:
    """Compose premises into a conclusion (conjunction, weakest-link, ceiling-capped)."""
    now = now or datetime.now(timezone.utc)
    prem = [p for p in premises if p is not None]

    if not prem:
        graph_hash = _graph_hash(conclusion.claim_id, ())
        cert = Certification(EvidenceStatus.UNVERIFIED, "synthesis(no premises)", False,
                             graph_hash, "no premises to synthesize", ("no premises to synthesize",))
        decision, tier, receipt, reasons = enforce(conclusion, cert, signing_key=signing_key)
        return ConclusionResult(
            conclusion, cert.status.value, decision, tier, (), graph_hash, receipt, reasons)

    # Seed the conclusion at its OWN ceiling so it can never exceed its verifiability class.
    seed = StatusDecision(
        claim_id=conclusion.claim_id,
        status=CEILING[conclusion.verifiability_class],
        execution_state=ExecutionState.OK,
        verifiability_class=conclusion.verifiability_class,
        direction=Direction.SUPPORTS,
        contested=False,
        support_lineages=0,
        has_definitive_pass=True,
        reasons=(),
        decided_at=now,
    )
    composed = compose(seed, [_premise_decision(p, now) for p in prem], now)

    nodes = tuple(SupportNode(
        claim_id=p.proposed.claim_id,
        status=p.certification.status.value,
        definitive_nonllm=p.certification.definitive_nonllm,
        record_hash=p.certification.record_hash,
    ) for p in prem)
    graph_hash = _graph_hash(conclusion.claim_id, nodes)

    trusted_derivation = derivation_proof_is_trusted(
        derivation_proof, subject=derivation_subject(conclusion, prem))
    all_definitive = trusted_derivation and all(node.definitive_nonllm for node in nodes)

    advisories = conclusion.advisories
    if not trusted_derivation:
        if derivation_proof is not None:
            advisory_name = "derivation_proof_invalid"
        else:
            advisory_name = "derivation_proof_required" if derivation_checked else "derivation_binding_unchecked"
        advisories = (*advisories, AdvisorySignal(
            advisory_name, Severity.CAUTION,
            "premises are certified but no valid system proof binds this conclusion to their exact hashes",),)
    concl_claim = dataclasses.replace(conclusion, advisories=advisories, verification_plan="synthesis")

    cert = Certification(
        status=composed.status,
        source=f"synthesis(conjunction) over {len(prem)} premises",
        definitive_nonllm=all_definitive,
        record_hash=graph_hash,
        detail="weakest-link conjunction; conclusion capped at its own verifiability ceiling",
        reasons=composed.reasons,
    )
    decision, tier, receipt, reasons = enforce(concl_claim, cert, signing_key=signing_key)
    return ConclusionResult(
        conclusion=concl_claim,
        composed_status=composed.status.value,
        release_decision=decision,
        risk_tier=tier,
        support_graph=nodes,
        graph_hash=graph_hash,
        receipt=receipt,
        reasons=reasons,
    )


def verify_conclusion(result: ConclusionResult, *,
                      signing_key: str | bytes | None = None) -> bool:
    """Re-derive the graph anchor from the support nodes and verify the receipt.
    Tampering any premise record_hash or the receipt fails this check (replayability)."""
    if _graph_hash(result.conclusion.claim_id, result.support_graph) != result.graph_hash:
        return False
    if result.receipt.claim_id != result.conclusion.claim_id:
        return False
    if result.receipt.cert_record_hash != result.graph_hash:
        return False
    return verify_receipt(result.receipt, signing_key=signing_key)
