"""The Verifier - the NON-LLM certifying core.

This is the only place a status can be MINTED, and it does so by delegating to the
existing, hardened, stdlib kernel - it does not reinvent a verifier:

  * "financial.sec_xbrl" -> glass_ionomer.lanes.financial.verify.verify_financial_claim,
    which routes a deterministic AUTHORITATIVE_LOOKUP through decide_status. VERIFIED
    there means only "the claimed value equals the figure the cited SEC filing reports."

The authoritative `sources` are passed in SEPARATELY from the claim, so a claim can
never carry (and thus self-authorise on) its own evidence.

`certify` is total and fail-closed: an unknown/"none" plan, a missing source, or any
exception resolves to an UNVERIFIED Certification with definitive_nonllm=False - never
an exception out of the engine, never an unjustified positive.
"""
from __future__ import annotations

from glass_ionomer.lanes.financial.models import FinancialClaim
from glass_ionomer.lanes.financial.verify import verify_financial_claim
from glass_ionomer.verify.status import EvidenceStatus

from .contracts import Certification, ProposedClaim


def _unverified(reason: str) -> Certification:
    return Certification(
        status=EvidenceStatus.UNVERIFIED,
        source="none",
        definitive_nonllm=False,
        record_hash="",
        detail=reason,
        reasons=(reason,),
    )


def certify(proposed: ProposedClaim, *, sources: dict) -> Certification:
    """Run the non-LLM verifier named by the claim's plan. `sources` holds the
    independent authoritative data (e.g. sources['companyfacts'])."""
    plan = proposed.verification_plan

    if plan == "none":
        return _unverified("no verification plan: claim could not be structured for a non-LLM verifier")

    if plan == "financial.sec_xbrl":
        companyfacts = (sources or {}).get("companyfacts")
        if not companyfacts:
            return _unverified("financial.sec_xbrl requires an authoritative companyfacts source; none supplied")
        p = proposed.payload
        try:
            fclaim = FinancialClaim(
                claim_id=proposed.claim_id,
                text=proposed.text,
                concept=p["concept"],
                claimed_value=float(p["claimed_value"]),
                unit=p.get("unit", "USD"),
                cik=p.get("cik"),
                ticker=p.get("ticker"),
                fiscal_year=p.get("fiscal_year"),
                fiscal_period=p.get("fiscal_period", "FY"),
                form=p.get("form"),
                binding_proof=p.get("binding_proof"),   # only a TRUSTED text->concept proof unlocks VERIFIED
            )
            pack = verify_financial_claim(fclaim, companyfacts)
        except Exception as exc:  # fail-closed: any lane error -> UNVERIFIED, never raise
            return _unverified(f"financial verifier error (fail-closed to UNVERIFIED): {type(exc).__name__}")
        # The financial lane is non-LLM by construction (deterministic AUTHORITATIVE_LOOKUP).
        # A positive/negative status from it therefore IS a non-LLM definitive signal.
        status = EvidenceStatus(pack.status)
        definitive = status in (EvidenceStatus.VERIFIED, EvidenceStatus.REFUTED)
        return Certification(
            status=status,
            source="sec_xbrl_companyfacts (non-LLM authoritative_lookup)",
            definitive_nonllm=definitive,
            record_hash=pack.manifest_hash,
            detail=pack.kernel_wording,
            reasons=tuple(pack.reasons),
        )

    if plan == "logic.propositional":
        from glass_ionomer.lanes.logic.verify import LogicClaim, verify_logic_claim
        p = proposed.payload
        try:
            lc = LogicClaim(
                claim_id=proposed.claim_id,
                text=proposed.text,
                mode=p["mode"],
                constraints=tuple(p.get("constraints", ())),
                target=p.get("target"),
                model=p.get("model"),
                binding_checked=bool(p.get("binding_checked", False)),
                binding_proof=p.get("binding_proof"),   # only a TRUSTED proof unlocks VERIFIED
            )
            res = verify_logic_claim(lc)
        except Exception as exc:  # fail-closed: any lane error -> UNVERIFIED, never raise
            return _unverified(f"logic verifier error (fail-closed to UNVERIFIED): {type(exc).__name__}")
        detail = res.detail
        if res.counterexample is not None:
            detail = f"{detail} | counterexample={res.counterexample}"
        return Certification(
            status=res.status,
            source="propositional_logic (non-LLM deterministic)",
            definitive_nonllm=res.definitive_nonllm,
            record_hash=res.record_hash,
            detail=detail,
            reasons=res.reasons,
        )

    return _unverified(f"unknown verification plan {plan!r}")
