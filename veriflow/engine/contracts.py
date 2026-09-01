"""Typed contracts for the unified VeriFlow engine (the best-of-all-worlds synthesis).

The engine fuses three bodies of prior work behind ONE honest pipeline:

  Reasoner  (DCER-derived, LLM/advisory, FAIL-OPEN)
      -> structures a raw output into a typed claim + a verification plan,
         and may attach advisory signals. It NEVER produces evidence/status.
  Verifier  (VeriFlow Lane A, NON-LLM, the certifying core)
      -> runs the existing decide_status kernel through a lane adapter; VERIFIED
         only on a non-LLM definitive verifier + checked claim-binding.
  Enforcer  (PXB-derived, DETERMINISTIC)
      -> maps the certified status (+ advisories) to a release decision and a
         signed, replayable receipt.

Load-bearing invariants (proved in tests/test_engine.py):
  1. No false VERIFIED          - only the kernel can mint VERIFIED.
  2. Agreement is not evidence  - advisory/LLM signals can only LOWER a release,
                                  never raise it (monotone restrictiveness).
  3. Fail-open reasoning,        - a Reasoner that cannot structure a claim yields
     fail-closed enforcement       UNVERIFIED (not a crash, not a release); a missing
                                   or errored certification escalates (does not release).
  4. Record, not judgment       - the receipt certifies that the decision followed
                                   deterministically from the recorded inputs; it does
                                   NOT certify the underlying judgment is correct.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from veriflow._canonical import canonical_sha256
from veriflow.verify.status import EvidenceStatus, VerifiabilityClass

ENGINE_VERSION = "veriflow.engine.v2"

# The single sentence consumers are allowed to attach to any release. It draws the
# integrity-of-record vs integrity-of-judgment line explicitly so the engine cannot
# be (mis)read as an oracle of truth.
DISCLAIMER = (
    "This receipt certifies that the release decision followed deterministically from "
    "the recorded inputs (integrity-of-record). It does NOT certify that the underlying "
    "claim is true (not integrity-of-judgment). VERIFIED means a non-LLM definitive "
    "verifier passed against a checked claim binding. Source-backed verification also "
    "requires authoritative, integrity-verified provenance. It is not a claim of economic "
    "or scientific truth. Model agreement is never evidence status."
)


# --------------------------------------------------------------------------- #
# Reasoner output
# --------------------------------------------------------------------------- #
class Severity(Enum):
    """Advisory severity. An advisory may only make a release MORE restrictive."""
    INFO = "info"        # observability only; no effect on the decision
    CAUTION = "caution"  # downgrade a release to at most allow_with_notice
    BLOCK = "block"      # force refuse


@dataclass(frozen=True)
class AdvisorySignal:
    """A fail-open Reasoner signal. Advisory by construction: it can lower a release,
    never raise it. An LLM 'this is true' belongs here as INFO and changes nothing."""
    name: str
    severity: Severity
    detail: str = ""


@dataclass(frozen=True)
class ProposedClaim:
    """What the Reasoner emits. It carries the STRUCTURED claim and the plan that
    names which non-LLM verifier should certify it - never the evidence itself
    (the claim must not supply its own authoritative record; that would self-authorise)."""
    claim_id: str
    text: str
    verifiability_class: VerifiabilityClass
    verification_plan: str            # e.g. "financial.sec_xbrl" | "none"
    payload: dict = field(default_factory=dict)   # plan-specific structured fields
    advisories: tuple = ()            # tuple[AdvisorySignal, ...]


# --------------------------------------------------------------------------- #
# Verifier output
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Certification:
    """The non-LLM Verifier's result, normalized from a lane adapter. `status` is the
    canonical kernel EvidenceStatus. `definitive_nonllm` records whether a non-LLM
    definitive verifier actually ran (the only thing that can justify a release)."""
    status: EvidenceStatus
    source: str                       # human-readable provenance of the verifier
    definitive_nonllm: bool
    record_hash: str = ""             # manifest/accession hash - the replay anchor
    detail: str = ""
    reasons: tuple = ()
    # True when every supporting stance behind this status was model-assigned
    # (kernel decide_status/compose propagate this); the enforcer floors such
    # certifications below release.
    model_derived_support_only: bool = False


# --------------------------------------------------------------------------- #
# Enforcer output
# --------------------------------------------------------------------------- #
class ReleaseDecision(Enum):
    ALLOW = "allow"                                # release / cite / act
    ALLOW_WITH_NOTICE = "allow_with_notice"        # release with a caveat (not VERIFIED)
    REQUIRE_CLARIFICATION = "require_clarification" # do not release; need more
    ESCALATE = "escalate"                          # do not release; human review
    REFUSE = "refuse"                              # do not release; refuted/blocked


# Restrictiveness order. Enforcer only ever moves UP this ladder, never down,
# which is exactly invariant (2): advisories cannot raise a release.
RESTRICTIVENESS = {
    ReleaseDecision.ALLOW: 0,
    ReleaseDecision.ALLOW_WITH_NOTICE: 1,
    ReleaseDecision.REQUIRE_CLARIFICATION: 2,
    ReleaseDecision.ESCALATE: 3,
    ReleaseDecision.REFUSE: 4,
}


@dataclass(frozen=True)
class Receipt:
    """A deterministic, replayable record of one engine decision. `receipt_hash` is a
    sha256 over the canonical content; an optional HMAC `signature` makes it tamper-evident.
    verify_receipt() re-derives the hash from the stored fields - same inputs, same hash."""
    engine_version: str
    claim_id: str
    raw_claim_sha256: str
    verification_plan: str
    cert_status: str
    cert_source: str
    cert_definitive_nonllm: bool
    cert_record_hash: str
    advisories: tuple                 # tuple[tuple[str, str], ...] -> (name, severity)
    release_decision: str
    risk_tier: int
    disclaimer: str
    receipt_hash: str
    signature: str | None = None


@dataclass(frozen=True)
class EngineResult:
    proposed: ProposedClaim
    certification: Certification
    release_decision: ReleaseDecision
    risk_tier: int
    receipt: Receipt
    reasons: tuple


# --------------------------------------------------------------------------- #
# Hashing helpers (the replay backbone)
# --------------------------------------------------------------------------- #
def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def canonical_hash(content: dict) -> str:
    """Type-preserving deterministic sha256 over supported structured content."""
    return canonical_sha256(content)


def legacy_canonical_hash(content: dict) -> str:
    """Receipt-hash algorithm used by ``veriflow.engine.v1``."""
    payload = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
