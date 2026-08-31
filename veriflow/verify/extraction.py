"""
Claim extraction + the claim-binding (round-trip) check.

Extraction is the hardest LLM-assisted step in the spine, and the one the
non-LLM `Verified` rule leans on: even a deterministic verifier only proves the
*formalization*, so we must independently confirm the formalization faithfully
represents the natural-language claim.

This module ships a deterministic, offline RULE-BASED extractor (arithmetic /
registry-lookup / heuristic classification) so the spine runs and is testable
with zero LLM. The real LLM extractor plugs in at the marked seam and MUST route
its model call through the gateway (veriflow.verify.gateway.guarded_model_call).

The binding check here is a genuine round-trip: parse the claim into a canonical
form, re-serialise it, and confirm it matches the normalised input. A claim whose
formalization does not round-trip is marked binding_ok=False and can never ground
`Verified` downstream.

Stdlib only.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import timedelta

from .safe_arith import ArithmeticBoundError, check_arithmetic_bounds
from .status import Claim, VerifiabilityClass

_OPINION_MARKERS = (
    "should", "ought", "best", "worst", "beautiful", "elegant",
    "i think", "in my view", "morally",
)
_LOOKUP_PREFIX = "lookup:"
_SYMBOL_MAP = {
    "\u2260": "!=",
    "\u2265": ">=",
    "\u2264": "<=",
    "\u00d7": "*",
    "\u00f7": "/",
    "\u2212": "-",
    "^": "**",
}

_SAFE_NODES = (
    ast.Expression, ast.Compare, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
    ast.USub, ast.UAdd, ast.Eq, ast.NotEq, ast.Lt, ast.Gt, ast.LtE, ast.GtE,
)


@dataclass(frozen=True)
class ExtractedClaim:
    claim: Claim
    formalization: str | None = None  # canonical relation string (mechanical arithmetic only)
    binding_ok: bool = False             # did the formalization round-trip back to the claim?
    binding_note: str = ""


def _normalise_arith(text: str) -> str:
    s = text.strip().rstrip(".")
    for k, v in _SYMBOL_MAP.items():
        s = s.replace(k, v)
    # single '=' -> '==', but leave '==', '<=', '>=', '!=' intact
    return re.sub(r"(?<![<>=!])=(?!=)", "==", s)


def _is_safe(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_NODES):
            return False
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return False
    return True


def _try_canonical_relation(text: str) -> str | None:
    """Return a canonical relation string if `text` is a safe arithmetic relation, else None."""
    norm = _normalise_arith(text)
    try:
        tree = ast.parse(norm, mode="eval")
    except (SyntaxError, RecursionError):
        return None
    try:
        check_arithmetic_bounds(tree)
    except (ArithmeticBoundError, RecursionError):
        return None
    if not isinstance(tree.body, ast.Compare) or not _is_safe(tree):
        return None
    try:
        return ast.unparse(tree).replace(" ", "")
    except RecursionError:
        return None


def classify(text: str) -> VerifiabilityClass:
    """Assign a verifiability class -> status ceiling. Conservative by design."""
    if text.strip().lower().startswith(_LOOKUP_PREFIX):
        return VerifiabilityClass.MECHANICAL
    if _try_canonical_relation(text) is not None:
        return VerifiabilityClass.MECHANICAL
    low = text.lower()
    if any(m in low for m in _OPINION_MARKERS):
        return VerifiabilityClass.INTERPRETIVE
    return VerifiabilityClass.EMPIRICAL


def extract_claim(text: str, *, cid: str = "c", scope: str = "",
                  ttl: timedelta | None = None, depends_on: tuple = ()) -> ExtractedClaim:
    """Deterministic, offline extraction. Produces a Claim + (for mechanical
    arithmetic) a round-trip-checked formalization."""
    vclass = classify(text)
    claim = Claim(id=cid, text=text, verifiability_class=vclass,
                  scope=scope, ttl=ttl, depends_on=depends_on)

    if text.strip().lower().startswith(_LOOKUP_PREFIX):
        return ExtractedClaim(claim=claim, formalization=None, binding_ok=True,
                              binding_note="registry lookup; binding via exact key match")

    canonical = _try_canonical_relation(text)
    if canonical is not None:
        norm_nospace = _normalise_arith(text).replace(" ", "")
        binding_ok = canonical == norm_nospace
        note = ("round-trip canonical match" if binding_ok
                else f"round-trip mismatch: {canonical!r} != {norm_nospace!r}")
        return ExtractedClaim(claim=claim, formalization=canonical,
                              binding_ok=binding_ok, binding_note=note)

    return ExtractedClaim(claim=claim, formalization=None, binding_ok=False,
                          binding_note=f"non-mechanical ({vclass.value}); evidence-based path")


# --- LLM extractor seam --------------------------------------------------- #
# def extract_claim_llm(text, *, gateway_decision, transport, provider):
#     """Real extractor. MUST go through the gateway:
#         from .gateway import guarded_model_call
#         raw = guarded_model_call(gateway_decision, provider, _prompt(text), transport=transport)
#     then parse `raw` into Claim(s) + formalization, and run the SAME binding
#     round-trip check before trusting any mechanical formalization."""
