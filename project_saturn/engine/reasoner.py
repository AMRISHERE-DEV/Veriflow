"""The Reasoner - DCER-derived, advisory, FAIL-OPEN.

This is the demoted descendant of the DCER signal stack. Its job is to STRUCTURE a
raw output into a typed claim and name the non-LLM verifier that should certify it,
plus optionally attach advisory signals (the place where any LLM judgment lives).

Two honesty rules are enforced by construction here:
  * The Reasoner never returns evidence or a status. It returns a ProposedClaim only.
  * It never supplies the authoritative record. The plan NAMES a verifier; the engine
    feeds that verifier an INDEPENDENT source (so a claim can't self-authorise).

Fail-open: if the input cannot be structured into a checkable claim, the Reasoner does
NOT raise and does NOT block - it returns a ProposedClaim with plan "none", which the
Verifier will resolve to UNVERIFIED. Reasoning failures degrade to "cannot certify",
never to a crash and never to a release.

Slice 1 ships a deterministic financial structurer (no live LLM needed). An LLM front
end can replace `propose_financial`'s body later; the contract and the invariants do
not change, because nothing the Reasoner emits can raise a release.
"""
from __future__ import annotations

from collections.abc import Sequence

from project_saturn.verify.status import VerifiabilityClass

from .contracts import AdvisorySignal, ProposedClaim, Severity


def propose_financial(
    *,
    claim_id: str,
    text: str,
    concept: str,
    claimed_value: float,
    fiscal_year: int | None,
    fiscal_period: str = "FY",
    unit: str = "USD",
    cik: str | None = None,
    ticker: str | None = None,
    form: str | None = None,
    binding_proof=None,                      # a TRUSTED text->concept proof is required for VERIFIED
    advisories: Sequence[AdvisorySignal] = (),
) -> ProposedClaim:
    """Structure a financial (SEC/XBRL) claim for the non-LLM Lane-A verifier.

    A financial figure-vs-filing claim is MECHANICAL (it can in principle reach
    VERIFIED). If the period is not fully specified we still emit the claim - the
    verifier will fail-closed to UNVERIFIED - but we attach a CAUTION advisory so the
    enforcer will not release it silently. The free-text -> us-gaap concept binding is
    likewise uncheckable in code, so without a TRUSTED binding_proof the claim caps below VERIFIED.
    """
    from project_saturn.lanes.financial.models import financial_binding_subject
    from project_saturn.verify.trust import binding_proof_is_trusted
    advisories = tuple(advisories)
    if fiscal_year is None:
        advisories = (*advisories,
            AdvisorySignal(
                name="period_underspecified",
                severity=Severity.CAUTION,
                detail="fiscal_year missing; the verifier cannot bind an exact filed entry",
            ),
        )
    subject = financial_binding_subject(
        claim_id=claim_id, text=text, concept=concept, claimed_value=claimed_value,
        unit=unit, cik=cik, ticker=ticker, fiscal_year=fiscal_year,
        fiscal_period=fiscal_period, form=form)
    if not binding_proof_is_trusted(binding_proof, subject=subject):
        advisories = (*advisories,
            AdvisorySignal(
                name="sec_text_concept_unchecked",
                severity=Severity.CAUTION,
                detail="the free-text -> us-gaap concept binding is not a trusted proof; caps below VERIFIED",
            ),
        )
    payload = {
        "concept": concept,
        "claimed_value": claimed_value,
        "unit": unit,
        "cik": cik,
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "form": form,
        "binding_proof": binding_proof,
    }
    return ProposedClaim(
        claim_id=claim_id,
        text=text,
        verifiability_class=VerifiabilityClass.MECHANICAL,
        verification_plan="financial.sec_xbrl",
        payload=payload,
        advisories=advisories,
    )


def propose_logic(
    *,
    claim_id: str,
    text: str,
    mode: str,                              # model_check | entailment | satisfiable | unsatisfiable
    constraints: Sequence = (),             # tuple of DSL exprs
    target=None,                            # DSL expr (entailment)
    model: dict | None = None,           # {var: bool} (model_check)
    binding_checked: bool = False,          # legacy/advisory only
    binding_proof=None,                     # a TRUSTED formula<->claim proof is required for VERIFIED
    advisories: Sequence[AdvisorySignal] = (),
) -> ProposedClaim:
    """Structure a propositional-logic claim for the non-LLM logic lane.

    A logic claim is MECHANICAL. The formula<->claim binding is uncheckable in code, so ONLY a
    trusted `binding_proof` (factory-issued) unlocks VERIFIED; without it a CAUTION is attached and
    the lane stays below VERIFIED (the deterministic check is advisory only)."""
    from project_saturn.lanes.logic.verify import logic_binding_subject
    from project_saturn.verify.trust import binding_proof_is_trusted
    advisories = tuple(advisories)
    subject = logic_binding_subject(
        claim_id=claim_id, text=text, mode=mode, constraints=tuple(constraints),
        target=target, model=model)
    if not binding_proof_is_trusted(binding_proof, subject=subject):
        advisories = (*advisories, AdvisorySignal(
            name="logic_binding_unchecked",
            severity=Severity.CAUTION,
            detail="the propositional formula is not confirmed (trusted) to encode the claim; check is advisory",
        ),)
    payload = {
        "mode": mode,
        "constraints": tuple(constraints),
        "target": target,
        "model": model,
        "binding_checked": binding_checked,
        "binding_proof": binding_proof,
    }
    return ProposedClaim(
        claim_id=claim_id,
        text=text,
        verifiability_class=VerifiabilityClass.MECHANICAL,
        verification_plan="logic.propositional",
        payload=payload,
        advisories=advisories,
    )


def propose_unstructured(
    *,
    claim_id: str,
    text: str,
    advisories: Sequence[AdvisorySignal] = (),
) -> ProposedClaim:
    """Fail-open fallback: the Reasoner could not map the input to any non-LLM
    verifier. The claim still flows, but verification_plan is "none", so the
    Verifier resolves it to UNVERIFIED and the Enforcer will not release it.
    Interpretive by class -> it can never be VERIFIED even if mis-routed."""
    return ProposedClaim(
        claim_id=claim_id,
        text=text,
        verifiability_class=VerifiabilityClass.INTERPRETIVE,
        verification_plan="none",
        payload={},
        advisories=tuple(advisories),
    )
