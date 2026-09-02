"""Project Saturn: claim-level evidence assurance and release gating for AI outputs.

Core invariant: model agreement is never evidence. VERIFIED requires a non-LLM
definitive verifier plus a trusted, content-bound claim binding; everything else
is graded under hard status ceilings, with abstention as the default.
"""

from .verify import (
    Claim,
    EvidenceItem,
    EvidenceStatus,
    ExecutionState,
    VerifiabilityClass,
    VerificationOutcome,
    verify_extracted,
    verify_text,
)

__version__ = "1.1.0"

__all__ = [
    "Claim",
    "EvidenceItem",
    "EvidenceStatus",
    "ExecutionState",
    "VerifiabilityClass",
    "VerificationOutcome",
    "verify_extracted",
    "verify_text",
]
