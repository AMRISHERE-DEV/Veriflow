"""Live-LLM Reasoner front end - DCER-as-fail-open, now with a real model.

This lets a raw natural-language sentence enter the engine: an LLM STRUCTURES it into
a checkable financial claim (concept / value / period), which the non-LLM Verifier then
checks against the authoritative record. The LLM is held strictly to the advisory,
fail-open contract - it cannot touch any invariant:

  * It only emits a ProposedClaim (structure + advisories). It never returns a status.
  * Its confidence is an INFO advisory: it changes NOTHING (agreement is not evidence).
  * The sentence -> concept/period BINDING is LLM-inferred and NOT independently checked,
    so an `llm_asserted_binding` CAUTION is always attached. Consequence: an LLM-structured
    claim whose value matches the filing remains UNVERIFIED / REQUIRE_CLARIFICATION, never
    ALLOW or ALLOW_WITH_NOTICE. Full ALLOW requires a checked binding (e.g. caller-asserted structured
    fields via propose_financial, or a future formalization check).
  * If the LLM errors, returns non-JSON, or cannot structure the sentence, we FAIL OPEN to
    an UNVERIFIED unstructured claim - never a crash, never a release.
  * Because this path never carries a trusted text-to-concept binding, an LLM-extracted value
    cannot produce VERIFIED or a definitive REFUTED; it remains UNVERIFIED.

The LLM is injected as `Callable[[str], str]` so this module is fully testable offline.
`default_llm()` wires the repo's existing env-gated live transport as the default.

Stdlib only.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from .contracts import AdvisorySignal, ProposedClaim, Severity, sha256_text
from .reasoner import propose_financial, propose_unstructured

LLM = Callable[[str], str]

_EXTRACTION_PROMPT = """You convert ONE sentence into a STRUCTURED financial claim so a
non-LLM program can check it against official SEC XBRL filings. You do NOT decide whether
the sentence is true. Extract only what the sentence asserts.

Return ONLY a single JSON object (no prose, no code fence) with these keys:
  "is_financial_fact": boolean   // true only if the sentence asserts a specific company financial figure for a period
  "concept": string              // the US-GAAP XBRL tag, e.g. "RevenueFromContractWithCustomerExcludingAssessedTax" or "NetIncomeLoss"
  "claimed_value": number        // the figure in BASE units (e.g. 383300000000 for "$383.3 billion")
  "unit": string                 // e.g. "USD"
  "fiscal_year": integer or null
  "fiscal_period": string        // one of "FY","Q1","Q2","Q3","Q4"
  "cik": string or null
  "ticker": string or null
  "confidence": number           // 0..1, your confidence in the EXTRACTION (not in the truth)
  "notes": string

Sentence:
<<<%s>>>
JSON:"""


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON parse of an LLM reply. Returns None (never raises) on failure."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        nl = t.find("\n")
        if nl != -1 and t[:nl].strip().lower() in ("json", ""):
            t = t[nl + 1:]
    for candidate in (t, _braces(t)):
        if candidate is None:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            continue
    return None


def _braces(t: str) -> str | None:
    i, j = t.find("{"), t.rfind("}")
    return t[i:j + 1] if 0 <= i < j else None


def propose_with_llm(raw: str, *, llm: LLM, claim_id: str | None = None) -> ProposedClaim:
    """Structure a raw sentence into a ProposedClaim using an injected LLM. Fail-open."""
    cid = claim_id or ("llm-" + sha256_text(raw)[:12])

    try:
        reply = llm(_EXTRACTION_PROMPT % (raw or ""))
    except Exception as exc:  # fail-open: LLM transport failure -> unverified, never a crash
        return propose_unstructured(
            claim_id=cid, text=raw,
            advisories=(AdvisorySignal("llm_unavailable", Severity.CAUTION,
                                       f"LLM call failed ({type(exc).__name__}); fail-open to UNVERIFIED"),),
        )

    data = _extract_json(reply)
    if (not data or not data.get("is_financial_fact")
            or not data.get("concept") or data.get("claimed_value") is None):
        return propose_unstructured(
            claim_id=cid, text=raw,
            advisories=(AdvisorySignal("llm_could_not_structure", Severity.CAUTION,
                                       "LLM did not return a structured financial claim; fail-open to UNVERIFIED"),),
        )

    try:
        value = float(data["claimed_value"])
        fy = int(data["fiscal_year"]) if data.get("fiscal_year") is not None else None
    except (TypeError, ValueError):
        return propose_unstructured(
            claim_id=cid, text=raw,
            advisories=(AdvisorySignal("llm_bad_fields", Severity.CAUTION,
                                       "LLM returned unpar-seable value/year; fail-open to UNVERIFIED"),),
        )

    conf = data.get("confidence")
    advisories = (
        # The binding is LLM-inferred and unchecked -> status remains UNVERIFIED.
        AdvisorySignal("llm_asserted_binding", Severity.CAUTION,
                       "the sentence->concept/period mapping was inferred by an LLM and is not "
                       "independently checked; the value match is non-LLM, the binding is not"),
        # Confidence is observability ONLY: INFO can never raise a decision.
        AdvisorySignal("llm_extraction_confidence", Severity.INFO,
                       f"llm extraction confidence={conf!r}; notes={str(data.get('notes',''))[:160]!r}"),
    )
    return propose_financial(
        claim_id=cid,
        text=raw,
        concept=str(data["concept"]),
        claimed_value=value,
        fiscal_year=fy,
        fiscal_period=str(data.get("fiscal_period", "FY") or "FY"),
        unit=str(data.get("unit", "USD") or "USD"),
        cik=(str(data["cik"]) if data.get("cik") else None),
        ticker=(str(data["ticker"]) if data.get("ticker") else None),
        advisories=advisories,
    )


def default_llm(*, provider: str = "anthropic", **transport_kw) -> LLM:
    """No provider transport ships in this cut - inject your own callable.

    Pass any ``prompt -> text`` callable as the ``llm`` argument of
    ``propose_with_llm`` instead. Structuring stays advisory and fail-open in
    every configuration: nothing the callable returns can reach VERIFIED,
    REFUTED, or an allow-release decision on its own."""
    raise NotImplementedError(
        "no bundled LLM transport: pass your own prompt->text callable "
        "(e.g. propose_with_llm(..., llm=my_callable))"
    )
