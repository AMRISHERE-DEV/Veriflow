"""Financial-lane verifier.

Routes a FinancialClaim through the HARDENED veriflow.verify VERIFIED gate. There
is NO new status logic here: the kernel (decide_status) decides. The lane's job is
to (a) deterministically locate THE authoritative filed figure for the EXACT
concept + period and (b) present an exact-match as a non-LLM definitive verifier
result with a checked claim-binding.

VERIFIED here means exactly: "the claimed value equals the figure reported for
this us-gaap concept and period in the cited SEC filing (accession)." It is NOT a
claim of economic truth, and the concept<->text binding is the caller's structured
assertion (Lane A verifies the tuple; free-text->concept anchoring is Lane B).

Hardened against the Increment-1 red-team (two rounds):
  * period must be fully specified and matched EXACTLY (no None wildcard);
  * value equality is EXACT: integers compared exactly, non-integers via a FIXED
    sub-cent ABSOLUTE tolerance -- never a magnitude-scaled relative window, and
    never claimant-tunable (kills the "off by $2M still verifies" class);
  * restatements/amendments are detected BEFORE any claimant-narrowable filter,
    across same-end rows AND any under-tagged row (fail-closed), with the LATEST
    own-period accession bound on a clean match;
  * the verified concept and unit are named in the provenance.

Stdlib only.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

from veriflow.verify.status import (
    AuthorityTier,
    Claim,
    EvidenceStatus,
    StatusPolicy,
    VerifiabilityClass,
    VerifierKind,
    VerifierOutcome,
    VerifierResult,
    decide_status,
)
from veriflow.verify.trust import binding_proof_is_trusted

from .extractor import extract_facts
from .models import (
    AssuranceTier,
    EvidencePack,
    FinancialClaim,
    SecFact,
    companyfacts_are_trusted,
    stable_hash,
)

CANDIDATE_METHOD = "sec_xbrl_companyfacts.v5"
# Value equality is EXACT. Integers compare exactly; non-integers compare with a tiny fixed
# ABSOLUTE epsilon that absorbs only parse/representation round-off, never a magnitude-scaled
# dollar window and never claimant-tunable. Restatement-distinctness is a SEPARATE, fully exact
# test (never this epsilon).
_ABS_EPS = 1e-6
# Above 2**53, float cannot represent every integer dollar exactly -> not verifiable.
_EXACT_INT_LIMIT = 2 ** 53


def _norm_fy(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _norm_fp(x):
    if x is None:
        return None
    s = str(x).strip().upper()
    return s or None


def _norm_cik(value) -> str | None:
    if value is None:
        return None
    try:
        number = int(str(value).strip().upper().replace("CIK", ""))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return f"{number:010d}"

_WORDING = {
    EvidenceStatus.VERIFIED: ("VERIFIED: claimed value equals us-gaap:{concept} ({unit}) for {fp}{fy} as reported "
                              "in {accn} ({form}). Not a claim of economic truth; concept<->text binding is separately attested."),
    EvidenceStatus.REFUTED: "REFUTED: claimed value does not equal us-gaap:{concept} ({unit}) for {fp}{fy} reported in {accn} ({form}).",
    EvidenceStatus.CONTESTED: ("CONTESTED: the filing record reports more than one value for us-gaap:{concept} ({unit}) {fp}{fy} "
                               "(restatement/amendment); not eligible for VERIFIED."),
    EvidenceStatus.UNVERIFIED: "UNVERIFIED: no single authoritative filed figure binds this claim.",
}


def _equal(a: float, b: float) -> bool:
    """Exact for integer-valued amounts; for non-integers, never bridge a sign inversion, and
    otherwise allow only float round-off (a tiny fixed absolute epsilon). Callers guarantee |a|,|b| < 2**53."""
    if float(a).is_integer() and float(b).is_integer():
        return int(a) == int(b)
    if a != 0 and b != 0 and (a > 0) != (b > 0):
        return False
    return abs(a - b) <= _ABS_EPS


def _period_facts(facts: list[SecFact], fy, fp) -> list[SecFact]:
    """Facts that report the claim's exact fiscal period.

    SEC ``fy``/``fp`` identify the filing, not each comparative fact inside it.
    One FY2023 10-K can therefore label its FY2021, FY2022, and FY2023 duration
    rows identically. For each accession, the row with the latest valid end date
    is the filing's own period; earlier rows are comparisons, not restatements.

    Amendment protection remains fail-closed: every accession contributes its
    own-period row, incomplete rows sharing that end still collide, and a wholly
    unlabelled amendment with no end is retained. Explicitly different periods
    (for example Q4 and FY) never collide, and ``form`` never prunes amendments.
    """
    want_fy, want_fp = _norm_fy(fy), _norm_fp(fp)
    labelled = [f for f in facts if _norm_fy(f.fiscal_year) == want_fy and _norm_fp(f.fiscal_period) == want_fp]
    if not labelled:
        return []
    by_accession: dict[str, list[SecFact]] = {}
    for fact in labelled:
        by_accession.setdefault(fact.accession, []).append(fact)

    own_period: list[SecFact] = []
    for accession_facts in by_accession.values():
        valid_ends = [
            fact.end for fact in accession_facts
            if isinstance(fact.end, str) and _valid_iso_date(fact.end)
        ]
        if not valid_ends:
            own_period.extend(accession_facts)
            continue
        latest_end = max(valid_ends)
        own_period.extend(
            fact for fact in accession_facts
            if fact.end == latest_end or not _valid_iso_date(fact.end)
        )

    ends = {f.end for f in own_period if f.end}
    extra = []
    for fact in facts:
        if fact in own_period:
            continue
        fact_fy, fact_fp = _norm_fy(fact.fiscal_year), _norm_fp(fact.fiscal_period)
        wholly_unlabelled = fact_fy is None and fact_fp is None
        incomplete_same_end = (
            bool(fact.end and fact.end in ends)
            and (fact_fy is None or fact_fp is None)
        )
        unplaced_amendment = (
            wholly_unlabelled
            and not fact.end
            and _norm_fp(fact.form) is not None
            and _norm_fp(fact.form).endswith("/A")
        )
        if incomplete_same_end or unplaced_amendment:
            extra.append(fact)
    return own_period + extra


def _valid_iso_date(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.year >= 1900


def _distinct_values(period: list[SecFact]) -> list[float]:
    # EXACT distinctness, independent of the match epsilon: two filings reporting different values
    # are a restatement regardless of how close, so the CONTESTED gate cannot be collapsed.
    out: list[float] = []
    for f in period:
        if not any(f.value == d for d in out):
            out.append(f.value)
    return out


def _manifest(claim: FinancialClaim, fact: SecFact | None, status: str,
              tier: AssuranceTier, reasons: tuple) -> str:
    claimed_value = (
        claim.claimed_value if math.isfinite(claim.claimed_value)
        else f"nonfinite:{claim.claimed_value!r}"
    )
    return stable_hash({
        "method": CANDIDATE_METHOD,
        "claim": {
            "id": claim.claim_id, "text": claim.text, "concept": claim.concept,
            "unit": claim.unit, "value": claimed_value, "fy": claim.fiscal_year,
            "fp": claim.fiscal_period, "form": claim.form,
            "cik": claim.cik, "ticker": claim.ticker,
        },
        "fact": None if fact is None else {
            "concept": fact.concept, "value": fact.value, "unit": fact.unit,
            "fy": fact.fiscal_year, "fp": fact.fiscal_period, "form": fact.form,
            "start": fact.start, "end": fact.end, "frame": fact.frame,
            "accession": fact.accession,
        },
        "status": status, "tier": tier.value, "reasons": list(reasons),
    })


def _pack(claim: FinancialClaim, status: EvidenceStatus, tier: AssuranceTier, lane: str,
          fact: SecFact | None, reasons: list[str], *, as_reported_in: str | None) -> EvidencePack:
    reasons_t = tuple(reasons)
    wording = _WORDING.get(status, status.value)
    if fact is not None:
        # period in the sentence comes from the CLAIM (the bound fact may be an under-tagged
        # amendment with fy/fp=None); concept/unit/accn/form come from the bound fact.
        wording = wording.format(concept=fact.concept, unit=fact.unit, accn=fact.accession,
                                 form=fact.form, fp=claim.fiscal_period, fy=claim.fiscal_year)
    return EvidencePack(
        claim_id=claim.claim_id, status=status.value, tier=tier, lane=lane,
        matched_fact=fact, as_reported_in=as_reported_in, reasons=reasons_t,
        manifest_hash=_manifest(claim, fact, status.value, tier, reasons_t),
        kernel_wording=wording,
    )


def verify_financial_claim(claim: FinancialClaim, companyfacts: dict,
                           *, now: datetime | None = None) -> EvidencePack:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    def unverified(msg: str) -> EvidencePack:
        reasons.append(msg)
        return _pack(claim, EvidenceStatus.UNVERIFIED, AssuranceTier.L0, "none", None, reasons, as_reported_in=None)

    if not math.isfinite(claim.claimed_value):
        return unverified("non-finite claimed value (inf/nan) - not eligible for any positive status")
    if abs(claim.claimed_value) >= _EXACT_INT_LIMIT:
        return unverified("claimed value too large for exact verification (>= 2**53)")
    if claim.fiscal_year is None or claim.fiscal_period is None:
        return unverified("period not fully specified (fiscal_year and fiscal_period required) - "
                          "cannot bind to an exact filed entry")
    if claim.cik is not None:
        claim_cik = _norm_cik(claim.cik)
        record_cik = _norm_cik((companyfacts or {}).get("cik"))
        if claim_cik is None:
            return unverified(f"claim CIK {claim.cik!r} is invalid")
        if record_cik is None:
            return unverified("companyfacts record has no valid top-level CIK; entity binding cannot be checked")
        if claim_cik != record_cik:
            return unverified(f"companyfacts CIK {record_cik} does not match claim CIK {claim_cik}")

    facts = extract_facts(companyfacts, claim.concept, claim.unit)
    if not facts:
        return unverified(f"no authoritative fact for concept {claim.concept!r} ({claim.unit})")

    period = _period_facts(facts, claim.fiscal_year, claim.fiscal_period)
    if not period:
        return unverified(f"concept present but no filed entry for {claim.fiscal_period} {claim.fiscal_year}")
    if any(abs(f.value) >= _EXACT_INT_LIMIT for f in period):
        return unverified("filed value too large for exact verification (>= 2**53)")

    # Restatement / amendment: >1 economically distinct filed value for the EXACT period -> never VERIFIED.
    distinct = _distinct_values(period)
    if len(distinct) > 1:
        latest = max(period, key=lambda f: f.accession)
        reasons.append(f"{len(distinct)} distinct filed values for {claim.fiscal_period} {claim.fiscal_year} "
                       f"(restatement/amendment) - not eligible for VERIFIED")
        return _pack(claim, EvidenceStatus.CONTESTED, AssuranceTier.L0, "A", latest, reasons, as_reported_in=None)

    # Single distinct value: bind the LATEST own-period accession (provenance points at the claim's
    # own-period filing, not a same-end sibling-period row).
    own = [f for f in period
           if _norm_fy(f.fiscal_year) == _norm_fy(claim.fiscal_year)
           and _norm_fp(f.fiscal_period) == _norm_fp(claim.fiscal_period)]
    fact = max(own or period, key=lambda f: f.accession)
    matched = _equal(claim.claimed_value, fact.value)

    result = VerifierResult(
        verifier_id="sec_xbrl",
        kind=VerifierKind.AUTHORITATIVE_LOOKUP,
        outcome=VerifierOutcome.PASS if matched else VerifierOutcome.FAIL,
        is_llm=False,
        source_authority=AuthorityTier.AUTHORITATIVE,    # EDGAR is the official primary source
        # Provenance: authoritative ONLY when the companyfacts came from a trusted source (the EDGAR
        # fetcher or an audited fixture) - a caller-supplied plain dict is forgeable and cannot verify.
        source_integrity_verified=companyfacts_are_trusted(companyfacts),
        # The STRUCTURED binding (concept+period+value -> the exact filed datapoint) is checked in code,
        # but the free-text -> concept binding is NOT: it needs a TRUSTED BindingProof to reach VERIFIED,
        # otherwise the claim caps below VERIFIED (kills the "net income"=GrossProfit overclaim).
        formalization_checked=binding_proof_is_trusted(
            claim.binding_proof, subject=claim.binding_subject()),
        applicable=True,
        detail=(f"us-gaap:{claim.concept} ({claim.unit}) {claim.fiscal_period}{claim.fiscal_year}: "
                f"claimed {claim.claimed_value} vs filed {fact.value} (accn {fact.accession})"),
    )
    kclaim = Claim(id=claim.claim_id, text=claim.text, verifiability_class=VerifiabilityClass.MECHANICAL)
    decision = decide_status(kclaim, [result], [], now=now, policy=StatusPolicy())
    status = decision.status
    reasons.extend(decision.reasons)

    tier = AssuranceTier.L3 if status is EvidenceStatus.VERIFIED else AssuranceTier.L0
    as_reported = fact.accession if status in (EvidenceStatus.VERIFIED, EvidenceStatus.REFUTED) else None
    return _pack(claim, status, tier, "A", fact, reasons, as_reported_in=as_reported)


def verify_prose_claim(claim_id: str, text: str) -> EvidencePack:
    """Lane B placeholder. A prose/interpretive financial claim is quote-located at best
    and can NEVER be VERIFIED by construction (the cap). Increment 1 returns the honest
    capped result; quote-anchoring + candidate coverage land in a later increment."""
    fc = FinancialClaim(claim_id=claim_id, text=text, concept="", claimed_value=0.0)
    reasons = ["prose/interpretive claim: Lane B (quote-anchored) not yet wired; "
               "capped below VERIFIED by construction"]
    return _pack(fc, EvidenceStatus.UNVERIFIED, AssuranceTier.L1, "B", None, reasons, as_reported_in=None)
