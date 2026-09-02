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

## Class 6 - Float leakage in the exact-rational arithmetic lane (found by kernel-benchmark design review, 2026-09-03)

Decimal literals were converted to exact rationals but integer literals were
not, so integer division produced a float that was then compared against an
exact rational: on v1.1.1, `1 / 10 = 0.1` was REFUTED and `1 / 10 > 0.1` was
VERIFIED - wrong-direction definitive verdicts on the README quickstart path,
the mirror image of Class 1. Non-integer exponents also fell back to float
silently, refuting true identities such as `(2 ** 0.5) ** 2 = 2`.

Fix (v1.1.2): every relation literal is an exact rational, so division is
exact end to end; a non-integer exponent is declared not exactly computable
and the verifier abstains (UNVERIFIED) rather than certifying or refuting on
float luck.

Public pins: `tests/test_arith_exact_rational.py`.

## Class 7 - Ticker without CIK never bound the entity (found by kernel-benchmark design review, 2026-09-03)

The financial lane compared CIKs only when the claim carried one. A claim
naming a ticker but no CIK, with a trusted text->concept binding proof,
verified against ANOTHER filer's trusted companyfacts whose same concept and
period held the same value - and the engine released it (VERIFIED / ALLOW /
tier 0). GCAB never saw this because its runner always supplies the CIK.

Fix (v1.1.2): a ticker is an entity assertion; if present without a resolved
CIK the entity is unbound and the claim stays UNVERIFIED with an explicit
reason. Claims carrying a CIK are unchanged. Whether entity-less tuple claims
(no ticker, no CIK) should also require an entity is recorded as an open
ratification item for the kernel benchmark, not changed silently.

Public pins: `tests/test_entity_binding.py`.

## Class 8 - Comparative rows bound by calendar year, not fiscal identity (found by external Codex review, 2026-09-03)

The Class 5 guard excluded a filing's own-period row but left the calendar
assumption in place for genuine comparatives: selection still tested whether
the period END fell in the claimed calendar year. For an offset fiscal calendar
(retail FYE early February), the FY2022 comparative ends in calendar 2023, so a
FY2023 claim carrying FY2022's value was VERIFIED and the true FY2023 value was
REFUTED - both wrong-period decisions, reproduced on v1.1.2.

Fix (v1.1.3): a comparative's fiscal identity is inferred from the issuer's own
context, never from the calendar: the filing's fy label anchors its own period,
and a comparative ending a whole number of fiscal years earlier (within a
52/53-week slack) is that many years back. Rows that do not fit that structure
stay unbound; if two filings disagree about the same period's identity (a
fiscal-year change), the lane abstains. Calendar-aligned filers (ACRES) and the
Class 5 own-period guard are unchanged.

Public pins: `tests/test_financial_period_fallback.py`
(genuine_comparative_cannot_verify_wrong_fiscal_year,
correct_fiscal_value_cannot_be_refuted_using_previous_year,
fiscal_year_change_makes_comparative_identity_ambiguous).

## Scope note

The audits above were author-conducted; no external party has yet audited this
system. Independent review is an open obligation, not a completed one. The
research tree's audit trail (additional lanes and their pins) ships as those
lanes harden.
