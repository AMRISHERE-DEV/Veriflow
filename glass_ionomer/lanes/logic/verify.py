"""Propositional-logic verifier (Lane: logic.propositional) - stdlib, NON-LLM.

A deterministic evaluator over a tiny tuple DSL plus bounded model enumeration. It can
VERIFY and FALSIFY propositional claims and, on falsification, returns a concrete
COUNTEREXAMPLE (the falsifier organ's payload).

Honesty by construction (mirrors the financial lane):
  * The formula is STRUCTURED data the caller supplies - never scraped from text. Whether the
    formula faithfully encodes a natural-language claim is the BINDING question. If
    `binding_checked` is False the lane is INCONCLUSIVE, so neither VERIFIED nor REFUTED can be
    minted on an unchecked binding (an LLM-structured formula caps below VERIFIED, exactly like
    `llm_asserted_binding` in the financial path).
  * The verdict is routed through the single `decide_status` kernel as a DETERMINISTIC
    (intrinsic, non-LLM) VerifierResult. The lane mints nothing itself.
  * Enumeration is bounded; an over-large or malformed formula fails OPEN to INCONCLUSIVE
    (-> UNVERIFIED), never an exception, never a guessed status.

DSL (expressions are tuples):
  ("const", True|False) | ("var", name) | ("not", e) | ("and", e, e, ...) |
  ("or", e, e, ...) | ("imp", a, b) | ("iff", a, b) | ("xor", a, b)

Stdlib only.
"""
from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from glass_ionomer._canonical import CanonicalizationError
from glass_ionomer.engine.contracts import canonical_hash
from glass_ionomer.verify.status import (
    Claim,
    EvidenceStatus,
    StatusPolicy,
    VerifiabilityClass,
    VerifierKind,
    VerifierOutcome,
    VerifierResult,
    decide_status,
)
from glass_ionomer.verify.trust import BindingProof, binding_proof_is_trusted

MAX_VARS = 18  # 2**18 = 262144 assignments; above this we abstain (fail-open) rather than churn
MAX_DEPTH = 512
MAX_NODES = 10000
MAX_EVAL_STEPS = 1_000_000


# --------------------------------------------------------------------------- #
# Deterministic evaluator
# --------------------------------------------------------------------------- #
def _eval(expr, env: dict) -> bool:
    if not isinstance(expr, (tuple, list)) or not expr:
        raise ValueError(f"malformed expr: {expr!r}")
    op = expr[0]
    if op == "const":
        return bool(expr[1])
    if op == "var":
        name = expr[1]
        if name not in env:
            raise KeyError(f"unbound variable {name!r}")
        return bool(env[name])
    if op == "not":
        return not _eval(expr[1], env)
    if op == "and":
        return all(_eval(x, env) for x in expr[1:])
    if op == "or":
        return any(_eval(x, env) for x in expr[1:])
    if op == "imp":
        return (not _eval(expr[1], env)) or _eval(expr[2], env)
    if op == "iff":
        return _eval(expr[1], env) == _eval(expr[2], env)
    if op == "xor":
        return _eval(expr[1], env) != _eval(expr[2], env)
    raise ValueError(f"unknown operator {op!r}")


def _collect_vars(expr, acc: set) -> None:
    if not isinstance(expr, (tuple, list)) or not expr:
        raise ValueError(f"malformed expr: {expr!r}")
    op = expr[0]
    if op == "var":
        acc.add(expr[1])
    elif op == "const":
        return
    else:
        for child in expr[1:]:
            _collect_vars(child, acc)


def _all_vars(exprs) -> tuple[str, ...]:
    acc: set = set()
    for e in exprs:
        _collect_vars(e, acc)
    return tuple(sorted(acc))


def _assignments(varnames):
    for bits in itertools.product((False, True), repeat=len(varnames)):
        yield dict(zip(varnames, bits, strict=True))


def _check_shape(exprs) -> int:
    """Iterative structural guard: nested/cyclic formulas abstain instead of recursing."""
    stack = [(e, 1) for e in exprs if e is not None]
    nodes = 0
    while stack:
        expr, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise ValueError(f"formula too large ({nodes} nodes > {MAX_NODES})")
        if depth > MAX_DEPTH:
            raise ValueError(f"formula too deep ({depth} > {MAX_DEPTH})")
        if not isinstance(expr, (tuple, list)) or not expr:
            raise ValueError(f"malformed expr: {expr!r}")
        op = expr[0]
        if op not in {"const", "var", "not", "and", "or", "imp", "iff", "xor"}:
            raise ValueError(f"unknown operator {op!r}")
        if op == "const":
            if len(expr) != 2 or not isinstance(expr[1], bool):
                raise ValueError("const requires exactly one boolean operand")
            children: Sequence[object] = ()
        elif op == "var":
            if len(expr) != 2 or not isinstance(expr[1], str) or not expr[1].strip():
                raise ValueError("var requires exactly one non-empty string name")
            children = ()
        elif op == "not":
            if len(expr) != 2:
                raise ValueError("not requires exactly one expression")
            children = expr[1:]
        elif op in {"imp", "iff", "xor"}:
            if len(expr) != 3:
                raise ValueError(f"{op} requires exactly two expressions")
            children = expr[1:]
        else:
            if len(expr) < 3:
                raise ValueError(f"{op} requires at least two expressions")
            children = expr[1:]
        if any(not isinstance(child, (tuple, list)) or not child for child in children):
            raise ValueError(f"{op} operands must be non-empty expressions")
        stack.extend((child, depth + 1) for child in children)
    return nodes


# --------------------------------------------------------------------------- #
# Claim / result
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LogicClaim:
    claim_id: str
    text: str
    mode: str                              # model_check | entailment | satisfiable | unsatisfiable
    constraints: tuple = ()                # tuple of DSL exprs (the premises / known facts)
    target: tuple | None = None         # DSL expr (for entailment)
    model: dict | None = None           # {var: bool} (for model_check)
    binding_checked: bool = False          # legacy/advisory flag; does NOT unlock VERIFIED on its own
    binding_proof: BindingProof | None = None  # a TRUSTED formula<->claim proof is required for VERIFIED/REFUTED

    def binding_subject(self) -> dict:
        return logic_binding_subject(
            claim_id=self.claim_id,
            text=self.text,
            mode=self.mode,
            constraints=self.constraints,
            target=self.target,
            model=self.model,
        )


def logic_binding_subject(*, claim_id: str, text: str, mode: str,
                          constraints=(), target=None, model=None) -> dict:
    """Canonical prose-to-formula bundle reviewed by a logic binding proof."""
    return {
        "lane": "logic.propositional",
        "claim_id": claim_id,
        "text": text,
        "mode": mode,
        "constraints": tuple(constraints),
        "target": target,
        "model": model,
    }


@dataclass(frozen=True)
class LogicResult:
    status: EvidenceStatus
    definitive_nonllm: bool
    record_hash: str
    counterexample: dict | None
    reasons: tuple
    detail: str


# --------------------------------------------------------------------------- #
# Decision (pure): returns ('pass'|'fail'|'abstain', counterexample, note)
# --------------------------------------------------------------------------- #
def _decide(claim: LogicClaim, *, node_count: int):
    mode = claim.mode
    if mode == "model_check":
        if not claim.model:
            return "abstain", None, "model_check requires a model"
        if not claim.constraints:
            return "abstain", None, "no constraints to check"
        for c in claim.constraints:
            if not _eval(c, claim.model):
                return "fail", {"violated_constraint": c, "model": dict(claim.model)}, \
                    "the model violates a constraint"
        return "pass", None, f"the model satisfies all {len(claim.constraints)} constraints"

    if mode == "entailment":
        if claim.target is None:
            return "abstain", None, "entailment requires a target"
        varnames = _all_vars((*tuple(claim.constraints), claim.target))
        if len(varnames) > MAX_VARS:
            return "abstain", None, f"too many variables ({len(varnames)} > {MAX_VARS}) for exact enumeration"
        estimated = (1 << len(varnames)) * max(1, node_count)
        if estimated > MAX_EVAL_STEPS:
            return "abstain", None, \
                f"exact enumeration work budget exceeded ({estimated} > {MAX_EVAL_STEPS})"
        any_premise_model = False
        for a in _assignments(varnames):
            if all(_eval(c, a) for c in claim.constraints):
                any_premise_model = True
                if not _eval(claim.target, a):
                    return "fail", {"counterexample": a}, "a model satisfies the premises but not the target"
        if not any_premise_model:
            return "abstain", None, "premises are unsatisfiable; entailment would be vacuous"
        return "pass", None, "every model of the premises satisfies the target"

    if mode in ("satisfiable", "unsatisfiable"):
        varnames = _all_vars(tuple(claim.constraints))
        if len(varnames) > MAX_VARS:
            return "abstain", None, f"too many variables ({len(varnames)} > {MAX_VARS}) for exact enumeration"
        estimated = (1 << len(varnames)) * max(1, node_count)
        if estimated > MAX_EVAL_STEPS:
            return "abstain", None, \
                f"exact enumeration work budget exceeded ({estimated} > {MAX_EVAL_STEPS})"
        witness = None
        for a in _assignments(varnames):
            if all(_eval(c, a) for c in claim.constraints):
                witness = a
                break
        if mode == "satisfiable":
            return ("pass", {"witness": witness}, "a satisfying model exists") if witness is not None \
                else ("fail", None, "no satisfying model exists (unsatisfiable)")
        # unsatisfiable
        return ("fail", {"counterexample": witness}, "a satisfying model exists (so it is not unsatisfiable)") \
            if witness is not None else ("pass", None, "no satisfying model: the constraints are contradictory")

    return "abstain", None, f"unknown logic mode {mode!r}"


def verify_logic_claim(claim: LogicClaim, *, now: datetime | None = None) -> LogicResult:
    now = now or datetime.now(timezone.utc)
    reasons = []

    # The formula<->claim binding is uncheckable in code (like the discovery lexical binding). ONLY a
    # trusted BindingProof (human/semantic/provenance, factory-issued) may unlock VERIFIED/REFUTED; a
    # bare `binding_checked=True` from an LLM/caller payload is an untrusted assertion and stays advisory.
    trusted = binding_proof_is_trusted(
        claim.binding_proof, subject=claim.binding_subject())

    try:
        node_count = _check_shape(
            tuple(claim.constraints) + ((claim.target,) if claim.target is not None else ()))
        verdict, counterex, note = _decide(claim, node_count=node_count)
    except Exception as exc:  # fail-open: malformed formula -> abstain, never raise
        verdict, counterex, note = "abstain", None, f"malformed logic claim ({type(exc).__name__})"

    if verdict in ("pass", "fail") and not trusted:
        # An untrusted binding cannot mint VERIFIED or REFUTED; the check is advisory only.
        reasons.append("formula-to-claim binding is not a trusted proof; deterministic check is advisory only")
        outcome = VerifierOutcome.INCONCLUSIVE
    elif verdict == "pass":
        outcome = VerifierOutcome.PASS
    elif verdict == "fail":
        outcome = VerifierOutcome.FAIL
    else:
        outcome = VerifierOutcome.INCONCLUSIVE
    reasons.append(note)

    vr = VerifierResult(
        verifier_id="propositional",
        kind=VerifierKind.DETERMINISTIC,   # intrinsic non-LLM definitive verifier (no source needed)
        outcome=outcome,
        is_llm=False,
        formalization_checked=trusted,
        applicable=True,
        detail=note,
    )
    kclaim = Claim(id=claim.claim_id, text=claim.text, verifiability_class=VerifiabilityClass.MECHANICAL)
    decision = decide_status(kclaim, [vr], [], now=now, policy=StatusPolicy())

    status = decision.status
    try:
        record_hash = canonical_hash({
            "lane": "logic.propositional",
            "mode": claim.mode,
            "constraints": claim.constraints,
            "target": claim.target,
            "model": claim.model,
            "binding_trusted": trusted,
            "outcome": outcome.value,
            "status": status.value,
        })
    except (CanonicalizationError, RecursionError, TypeError, ValueError):
        # Malformed/unbounded formulas already abstain. Anchor that failure without
        # recursively serializing the hostile structure a second time.
        record_hash = canonical_hash({
            "lane": "logic.propositional",
            "claim_id": claim.claim_id,
            "mode": claim.mode if isinstance(claim.mode, str) else type(claim.mode).__name__,
            "canonical_input": "rejected",
            "outcome": outcome.value,
            "status": status.value,
        })
    definitive = status in (EvidenceStatus.VERIFIED, EvidenceStatus.REFUTED)
    return LogicResult(
        status=status,
        definitive_nonllm=definitive,
        record_hash=record_hash,
        counterexample=counterex if verdict == "fail" and trusted else None,
        reasons=tuple(reasons) + tuple(decision.reasons),
        detail=note,
    )
