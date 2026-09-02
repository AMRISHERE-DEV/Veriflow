# Adversarial Audit History

This engine's development included structured red-team audits of its own
certification gate, conducted by the author with LLM agents used to generate
attacks (never to adjudicate them). Three classes of false-`VERIFIED` defect
were found in earlier versions, reproduced, fixed, and pinned as regression
tests. They are recorded here because in verification systems the dangerous
bugs are not crashes but quiet violations of the certification predicate.

## Class 1 — Magnitude-scaled tolerance

An equality comparison using a relative float window certified claims that
were off by whole dollars at trillion-dollar magnitudes. The class recurred
months later in a second comparator in the research tree (a relative
tolerance that spanned thousands of dollars at filed-figure scale and, in the
mirror direction, refuted factually true inequality claims).

Fix: exact comparison for integer-valued amounts; a fixed sub-cent absolute
epsilon (never caller-reachable) for representation round-off only.

Public pins: `tests/test_financial_audit_pins.py`;
`tests/primary_core/test_verify_hardening_contract.py` (exact arithmetic);
`tests/test_financial_lane.py`.

## Class 2 — Unbound formalization

An adapter marked the natural-language-to-specification binding as checked
without checking it, allowing a true-but-unrelated computation to certify an
unrelated statement. Subtler variants of the class: reusing a binding proof
minted for one claim on another, and mutating the specification after proof
issuance.

Fix: trusted binding proofs are content-bound — committed to a canonical hash
of the exact claim subject — and fail closed on any mutation; lexical token
matching is excluded from the trusted methods for prose-to-specification
binding.

Public pins: `tests/primary_core/test_verify_status_policy_contract.py`
(definitive pass without binding is not `VERIFIED`; LLM-only pass cannot
reach `VERIFIED`); `tests/primary_core/test_verify_quote_binding_contract.py`;
`tests/test_release_floors_and_trust.py` (serialization, reconstruction, and
replace() strip trust); `tests/test_engine.py` / `tests/test_engine_sec.py`
(binding gates in the release path).

## Class 3 — Self-declared provenance

An adapter accepted caller-declared authority and self-hashed records as a
trusted source, allowing certification from the data plane alone.

Fix: authority exists only as unforgeable issuance — source proofs are minted
by registered resolvers; caller-supplied records can never reach the
corroboration count, and provenance-required deployments reject them as
positive evidence entirely.

Public pins: `tests/test_production_hardening.py` (self-hashed records cannot
corroborate; arbitrary resolvers cannot mint provenance; source proofs bind to
exact content); `tests/primary_core/test_resolver_contract.py`.

## Class 4 - Fiscal-period label rigidity (found by blinded holdout, 2026-09-03)

A blinded 25-case holdout (development round two) surfaced a false ABSTENTION:
some filers' FY-N figure exists in EDGAR companyfacts only as comparative rows
inside later filings (labelled fy=N+1), so strict fy-label binding returned
UNVERIFIED for a true, consistently-filed value. Conservative direction - no
false certification - but a real coverage boundary.

Fix (v1.0.1): a fail-closed comparative fallback that runs only when zero rows
carry the claimed fy/fp label. Candidates are full-year duration rows whose
represented period ends in the claimed fiscal year; every candidate across all
accessions must agree exactly (any disagreement stays CONTESTED); multiple
distinct year-ends are ambiguous and stay unbound; quarters and instants never
fall back. The receipt states: "Bound via comparative rows; no original-filing
row present." The strict label doctrine remains the primary rule, and the
wrong-period refutation behavior is pinned unchanged.

Public pins: `tests/test_financial_period_fallback.py` (fallback verify /
refute / contested; label-exists inverse guard; wrong-period still refutes;
FY-only; ambiguous year-ends and partial-year durations fail closed).

## Class 5 - Offset-fiscal-calendar false VERIFIED in the comparative fallback (found by tri-agent source review, 2026-09-03)

The Class 4 fix itself contained a wrong-period path: for a filer whose fiscal
year ends in the NEXT calendar year (e.g. retail FYE early February), the
filing's own-period row (labelled fy=N, period ending in calendar N+1) could be
bound by the fallback to a claim about fiscal year N+1 - a reproduced
wrong-period false VERIFIED. Every Class 4 fixture used calendar-aligned
fiscal years, so the pins missed it; an external tri-agent review flagged the
calendar/fiscal-year edge and a targeted reproduction confirmed it.

Fix (v1.0.2): candidates must be strict COMPARATIVE rows - any row that is its
accession's own-period row (latest valid end in that filing) is excluded,
because its period already carries an authoritative label for a different
fiscal year. Legitimate comparative-row binding (rows ending before their
filing's own period end) is unchanged.

Public pins: `tests/test_financial_period_fallback.py`
(offset_fiscal_calendar_cannot_mint_wrong_period_verified,
own_period_rows_are_never_fallback_candidates).

## Scope note

The audits above were author-conducted; no external party has yet audited this
system. Independent review is an open obligation, not a completed one. The
research tree's audit trail (additional lanes and their pins) ships as those
lanes harden.
