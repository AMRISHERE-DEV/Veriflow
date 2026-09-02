"""Propositional-logic lane: a stdlib, NON-LLM definitive verifier.

Harvested (rewritten clean) from the AmrThink codebase
(dcer/verification/{z3_engine,consensus_verifier}, dcer/layers/truth_symbolic_verifier):
a deterministic propositional evaluator + bounded model enumeration, with the over-claim,
the regex NL->logic scraping, the LLM consensus, and the magic-confidence numbers all left
behind. No third-party deps - itertools/stdlib only.
"""
from .verify import LogicClaim, LogicResult, verify_logic_claim

__all__ = ["LogicClaim", "LogicResult", "verify_logic_claim"]
