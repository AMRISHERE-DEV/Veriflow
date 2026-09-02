"""Project Saturn unified engine: Reasoner (fail-open) -> Verifier (non-LLM core) -> Enforcer.

The best-of-all-worlds synthesis of DCER+PXB, AmrThink, and the Project Saturn Lane-A core,
behind one honest pipeline. See contracts.py for the load-bearing invariants.
"""
from __future__ import annotations

from project_saturn.verify.trust import DerivationProof, issue_trusted_derivation_proof

from .contracts import (
    DISCLAIMER,
    ENGINE_VERSION,
    AdvisorySignal,
    Certification,
    EngineResult,
    ProposedClaim,
    Receipt,
    ReleaseDecision,
    Severity,
)
from .enforcer import enforce, verify_receipt
from .lanes import certify
from .llm_reasoner import LLM, default_llm, propose_with_llm
from .pipeline import assure, assure_text
from .reasoner import propose_financial, propose_logic, propose_unstructured
from .sec import assure_sec_claim
from .synthesizer import (
    ConclusionResult,
    SupportNode,
    derivation_subject,
    synthesize,
    verify_conclusion,
)

__all__ = [
    "DISCLAIMER",
    "ENGINE_VERSION",
    "LLM",
    "AdvisorySignal",
    "Certification",
    "ConclusionResult",
    "DerivationProof",
    "EngineResult",
    "ProposedClaim",
    "Receipt",
    "ReleaseDecision",
    "Severity",
    "SupportNode",
    "assure",
    "assure_sec_claim",
    "assure_text",
    "certify",
    "default_llm",
    "derivation_subject",
    "enforce",
    "issue_trusted_derivation_proof",
    "propose_financial",
    "propose_logic",
    "propose_unstructured",
    "propose_with_llm",
    "synthesize",
    "verify_conclusion",
    "verify_receipt",
]
