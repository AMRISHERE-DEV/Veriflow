"""The unified engine pipeline - one entry point wiring the three layers.

    raw/structured claim
        -> Reasoner   (fail-open: structure + advise, never certify)
        -> Verifier   (non-LLM Lane A: the only place a status is minted)
        -> Enforcer   (deterministic: decision + signed replayable receipt)
        -> EngineResult

The authoritative `sources` are passed straight to the Verifier, never through the
claim, so nothing can self-authorise. `assure` is total: a bad claim or missing
source yields a non-releasing EngineResult, never an exception.

Stdlib only.
"""
from __future__ import annotations

from .contracts import EngineResult, ProposedClaim
from .enforcer import enforce
from .lanes import certify
from .llm_reasoner import LLM, propose_with_llm


def assure(
    proposed: ProposedClaim,
    *,
    sources: dict | None = None,
    signing_key: str | bytes | None = None,
) -> EngineResult:
    """Run a ProposedClaim end to end and return the release decision + receipt."""
    certification = certify(proposed, sources=sources or {})
    decision, tier, receipt, reasons = enforce(proposed, certification, signing_key=signing_key)
    return EngineResult(
        proposed=proposed,
        certification=certification,
        release_decision=decision,
        risk_tier=tier,
        receipt=receipt,
        reasons=reasons,
    )


def assure_text(
    raw: str,
    *,
    llm: LLM,
    sources: dict | None = None,
    claim_id: str | None = None,
    signing_key: str | bytes | None = None,
) -> EngineResult:
    """Full path from a raw sentence: LLM Reasoner structures it (fail-open, advisory-only),
    then the non-LLM Verifier + deterministic Enforcer decide. The LLM cannot mint a status
    or raise a release; a clean ALLOW is unreachable on this path because the binding is
    LLM-asserted (the status remains UNVERIFIED and requires clarification)."""
    proposed = propose_with_llm(raw, llm=llm, claim_id=claim_id)
    return assure(proposed, sources=sources, signing_key=signing_key)
