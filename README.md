# Project Saturn — claim assurance and release gating for AI outputs

> **Formerly VeriFlow.** Renamed in v1.1.0. The Python package is `project_saturn`; `import veriflow` still works as a deprecated alias for one release. Published artifacts and the whitepaper keep their original VeriFlow title.

> **Core invariant: model agreement is never evidence.** `VERIFIED` requires a non-LLM definitive
> verifier plus a trusted, content-bound claim binding — and when a claim cannot be checked, the
> engine abstains.

Project Saturn is a claim-level evidence-assurance and release-gating engine. Given a claim (and optional
evidence), it decides what evidence status the claim can honestly carry and whether it may be
released, cited, acted on, escalated, or blocked. Language models may reason, propose, and
interpret; only code-owned deterministic gates decide what can be called settled. It is not a truth
oracle: its job is to make unsupported certainty difficult and to produce a replayable, hash-bound
record of what was checked, what was not, and why.

This repository is the public reference cut: the deterministic verification kernel, the release
engine, and two working domain lanes (SEC/XBRL financial facts and propositional logic). It is
stdlib-only, offline by default, and deterministic — the one optional network call is the public
SEC EDGAR fetch in the demo (no API key involved).

## What `VERIFIED` means — and does not mean

`VERIFIED` is a narrow, scope-specific statement:

> An approved **non-LLM deterministic mechanism** checked the **exact bound claim** against the
> named authoritative record, and the mapping from the claim's text to what the verifier actually
> checked carries a **trusted binding proof**.

In the SEC/XBRL lane, for example, `VERIFIED` means precisely: *the claimed value equals the figure
reported for this US-GAAP concept and fiscal period in the cited SEC filing (the accession number is
the proof).* It is a matches-the-filed-figure statement.

`VERIFIED` does **not** mean:

- economic truth (a filed figure can be misleading and still match);
- universal truth, study quality, causal validity, or soundness of the surrounding argument;
- that any number of agreeing models endorsed the claim — model agreement never grounds status;
- that the surrounding prose is certified — only the exact bound tuple is.

A definitive pass that coexists with a credible contradiction is released as `CONTESTED`, never
`VERIFIED`. Failure to check ("no evidence", timeout, error) is `UNVERIFIED` with a recorded reason
— never a conclusion.

## Status ladder and ceilings

Seven evidence statuses, minted only by the kernel (`project_saturn.verify.decide_status`):

| Status | Predicate (summary) | Release decision |
| --- | --- | --- |
| `VERIFIED` | Non-LLM definitive verifier passed, claim binding checked and trusted, no credible contradiction | Allow |
| `CORROBORATED` | ≥ 2 independent, provenance-bound source lineages support the claim, no credible contradiction | Allow with notice |
| `SUPPORTED` | 1 admissible supporting lineage, no credible contradiction | Require clarification (one lineage is context, not release-grade support) |
| `CONTESTED` | Credible support and credible contradiction coexist | Escalate for review |
| `UNVERIFIED` | Required evidence or verifier result absent or inadmissible; could-not-check is not negative evidence | Require clarification |
| `EXPIRED` | A formerly positive, time-limited status whose TTL lapsed; must be re-verified | Require clarification |
| `REFUTED` | An approved, applicable, bound definitive verifier contradicted the claim in scope | Refuse |

Every claim is typed at extraction time with a verifiability class, which imposes a **hard status
ceiling** — a structural cap, not advice:

| Verifiability class | Meaning | Maximum status |
| --- | --- | --- |
| `MECHANICAL` | Decidable by a non-LLM mechanism (numeric, formal, registry lookup) | `VERIFIED` |
| `EMPIRICAL` | Supportable by evidence but not mechanically decidable (most scientific claims) | `CORROBORATED` |
| `INTERPRETIVE` | Qualitative, definitional, or contested by nature | `SUPPORTED` |

Interpretive claims cannot reach `CORROBORATED` by design: multiple sources agreeing on an
interpretation is consensus, which this system refuses to launder into evidence status. Composed
conclusions inherit the **weakest** admissible premise status; a conclusion is never more certain
than its weakest required premise.

## Certifiable claim types in this cut

Four claim shapes have a deterministic, non-LLM certification path in this repository. Everything
else caps below `VERIFIED` and travels the evidence ladder instead.

| # | Claim type | Example | Definitive mechanism | Module |
| --- | --- | --- | --- | --- |
| 1 | Arithmetic relation | `12 / 4 = 3` | Exact rational evaluation (no float tolerance), bounded AST | `project_saturn.verify.safe_arith` |
| 2 | Registry lookup | `lookup:key=expected` | Exact match against a system-trusted registry record | `project_saturn.verify.verifiers` |
| 3 | Propositional logic | Model-check / entailment / SAT / UNSAT in a tuple DSL | Bounded exhaustive model enumeration | `project_saturn.lanes.logic` |
| 4 | SEC/XBRL as-filed figure | "Entity X reported concept C = V for FY2023" | Exact match against trusted EDGAR companyfacts, restatement-aware, fail-closed | `project_saturn.lanes.financial` |

In every case, `VERIFIED` additionally requires the trusted claim-binding proof described below —
a correct check of the wrong formalization is not a verified claim. Further lanes (grounded
scientific predicates, typed adapter contracts, lab-workflow gates) exist in the research tree and
will ship here as they harden.

## Quickstart

Requires Python 3.10+. No dependencies.

```bash
pip install -e .

# flagship demo: three SEC/XBRL claims, offline by default
python examples/demo_sec_xbrl.py

# test suite (stdlib unittest)
python -m unittest discover -s tests -t .
```

Python API:

```python
from datetime import datetime, timezone

from project_saturn.verify.pipeline import verify_text
from project_saturn.verify.verifiers import ArithmeticVerifier

outcome = verify_text("2 + 2 = 4", [], [ArithmeticVerifier()],
                      now=datetime.now(timezone.utc))
print(outcome.decision.status)   # EvidenceStatus.VERIFIED
```

The demo is the honest tour: an exact filed figure with a trusted binding mints `VERIFIED`; the
same figure off by $42 is `REFUTED`; and the same *correct* figure **without** a binding proof caps
below `VERIFIED` — a value match alone is never enough.

## Model output is advisory, structurally

`project_saturn.engine` accepts an optional LLM callable for structuring free text into typed claims
(`engine.llm_reasoner`). It is bring-your-own and fail-open: no provider is bundled, a structuring
failure degrades to the deterministic path, and nothing a model emits can reach `VERIFIED`,
`REFUTED`, or an allow-release decision on its own. The unit-test suite runs fully offline and
proves this boundary (`tests/test_engine_llm.py`).

## Architecture at a glance

| Package | Responsibility |
| --- | --- |
| `project_saturn.verify` | The certifying kernel: extraction, admissibility, provenance boundary, trust proofs, status decision. Stdlib-only. |
| `project_saturn.engine` | Propose → certify → enforce: the sole mapping from evidence status to release decision and signed receipt. |
| `project_saturn.lanes.financial` | SEC/XBRL lane: EDGAR companyfacts resolution, deterministic fact extraction, fail-closed verification. |
| `project_saturn.lanes.logic` | Propositional-logic lane: bounded exhaustive model enumeration. |

Trust is object identity, not a boolean field: trusted proofs are stamped by private factories, do
not survive serialization, and cannot be reconstructed from attacker-supplied data. Binding proofs
are content-bound — a proof is valid only for the exact claim subject it was minted for, and fails
closed on any mutation. Receipts carry HMAC signatures when a signing key is configured; unsigned
hashes prove internal consistency, not tamper attestation.

## Limitations

Read these as output constraints, not footnotes — unresolved claims abstain or require
clarification.

- **Text-to-meaning binding is declared uncheckable in code.** Whether a claim's prose actually
  refers to the structured tuple a verifier checked (e.g. whether "net income" in a sentence means
  `us-gaap:NetIncomeLoss`) cannot be certified by token matching; lexical binding is deliberately
  excluded from the trusted methods. Reaching `VERIFIED` requires a trusted binding proof issued by
  provenance, checked semantics, or explicit human confirmation. Without one, a claim caps below
  `VERIFIED` even when the numbers match.
- **Empirical claims cap at `CORROBORATED`.** No volume of literature support, source agreement, or
  model consensus makes an empirical claim `VERIFIED`. Interpretive claims cap at `SUPPORTED`.
- **Specialist lanes, not general coverage.** SEC/XBRL and propositional logic are explicit APIs;
  there is no automatic general-purpose verifier selection.
- **This is a reference implementation, not a hosted service.** No SaaS endpoint, no SLA, no
  managed keys. EDGAR companyfacts are as-filed data, not an appraisal of the filer.

## Author and prior work

Project Saturn is authored by **Dr. Amr Elnaggar** (sole author; see `AUTHORS.md`). It descends from his
earlier published work on DCER+PXB, published on Zenodo (DOI: `10.5281/zenodo.18047498`) and the
subject of USPTO provisional patent application **63/940,036**. Project Saturn carries that lineage
forward as a standalone engine: the same separation of model reasoning from evidence authority,
hardened into the non-LLM `VERIFIED` gate, trusted content-bound binding proofs, and the
abstention-first release policy documented here.

## Citing

Whitepaper (preprint v1.4): Elnaggar, A. (2026). *VeriFlow: A Containment-First
Architecture for Evidence-Gated Release of Language-Model Claims.* Zenodo.
DOI [10.5281/zenodo.22233268](https://doi.org/10.5281/zenodo.22233268).

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
