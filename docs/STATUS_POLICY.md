# Evidence Status Policy

This is the canonical specification of evidence status in Glass Ionomer Verify. It is the
spine of the system: the UI, the API, the workflow, and the human-review rules
all read from it. It is intentionally written as **predicates**, not prose, so it
is testable. The reference implementation lives in
[`glass_ionomer/verify/status.py`](../glass_ionomer/verify/status.py); the tests in
[`tests/primary_core/test_verify_status_policy_contract.py`](../tests/primary_core/test_verify_status_policy_contract.py)
prove that each
status is reachable only via its own predicate.

---

## 1. Three orthogonal axes

A claim's verdict is **not** one label. It is three independent things, never
collapsed into one:

1. **Evidence status** - what the evidence/verifiers say (7 values, below).
2. **Execution state** - whether we could even check (`OK`, `NO_EVIDENCE`,
   `ERROR`, `TIMEOUT`, `BUDGET_EXHAUSTED`, `SKIPPED`).
3. **Contested flag + direction** - whether credible support and contradiction
   coexist, and which way the claim leans (`SUPPORTS`/`REFUTES`/`NEUTRAL`).

> **Why separate execution state.** "We could not verify" must never be encoded
> as an evidence conclusion. A `NO_EVIDENCE`/`ERROR`/`TIMEOUT`/`BUDGET_EXHAUSTED`
> result is `UNVERIFIED` with a *reason*, distinct from "we checked and it was
> genuinely inconclusive".

---

## 2. Verifiability class -> status ceiling

Assigned at **claim extraction time**. It is a *typed eligibility*: a claim that
is not mechanically checkable is **structurally incapable** of reaching
`Verified`. This is a feature: it forces honest ceilings.

| Class | Meaning | Max status |
|---|---|---|
| `MECHANICAL` | Decidable by a non-LLM mechanism (numeric, formal, registry-lookup, code-testable, signed-doc) | `VERIFIED` |
| `EMPIRICAL` | Supportable by evidence but not mechanically decidable (most scientific claims) | `CORROBORATED` |
| `INTERPRETIVE` | Qualitative / definitional / contested-by-nature | `SUPPORTED` |

> **Why `INTERPRETIVE` cannot be `CORROBORATED`.** Multiple sources agreeing on
> an interpretation is *consensus*, which is exactly the thing this system
> refuses to launder into evidence status. So interpretive claims top out at
> `SUPPORTED` by design.

The ceiling is a hard cap applied **after** the raw predicate fires: a candidate
status stronger than the ceiling is lowered to the ceiling, with a reason
recorded. (This is also a safety net against mis-classification.)

---

## 3. The seven evidence statuses (as predicates)

Let, over the **admissible** evidence (passed integrity + freshness + scope):

- `support_lineages` = count of **distinct independent source lineages** with
  entailment `ENTAILS` and strength >= `support_min_strength`.
- `credible_contradiction` = at least one admissible evidence item with entailment
  `CONTRADICTS` and strength >= `contradiction_min_strength`.
- `definitive_pass` = at least one verifier that is **non-LLM**, applicable, of a definitive
  kind, with a checked claim-binding (`formalization_checked`), outcome `PASS`
  (for *sourced* definitive kinds, also: authoritative tier + integrity verified).
- `definitive_fail` = the same, but outcome `FAIL`.

| Status | Predicate (after which the ceiling cap is applied) |
|---|---|
| **`REFUTED`** (in scope) | `definitive_fail` - a definitive applicable verifier contradicts the claim in its scope |
| **`VERIFIED`** | `definitive_pass` and no credible contradiction. A credible disputing source changes the release status to `CONTESTED` for review. |
| **`CORROBORATED`** | not verified/refuted, `support_lineages >= 2`, and not `credible_contradiction` |
| **`SUPPORTED`** | not above, `support_lineages >= 1`, and not `credible_contradiction` |
| **`CONTESTED`** | credible support **and** credible contradiction coexist |
| **`UNVERIFIED`** | none of the above - insufficient evidence, inadequate coverage, or no valid verifier outcome (carry the execution-state reason) |
| **`EXPIRED`** | a previously-positive status whose TTL has lapsed; must be re-verified (see Section 5) |

### The non-LLM rule (enforced, not advisory)

`Verified` requires a **non-LLM** definitive verifier. An LLM `PASS`, however
many models agree, never grounds `Verified`; it falls through to the
evidence-based ladder (`CORROBORATED`/`SUPPORTED`). LLMs may assist with
extraction, triage, passage identification, and entailment, but the
`generator_overlap` between the models that produced a claim and the models that
"verify" it makes LLM agreement an unreliable independent signal.

Model-derived entailment is tracked on each evidence item. It may contribute to the
evidence ladder, but when every supporting stance is model-derived the decision is marked
`model_derived_support_only`. Synthesis keeps that claim out of `settled_facts` and exposes
the need for non-model entailment review.

### The claim-binding rule

Even a deterministic verifier may sit behind an LLM-mediated NL-to-formal translation.
`Verified` therefore additionally requires `formalization_checked = True`, derived at
the engine-owned adapter boundary from deterministic extraction or a system-issued proof
bound to the exact claim subject. The mapping from the natural-language claim to *what the
verifier actually checked* must be verified (round-trip paraphrase match,
schema-constrained extraction, or human confirmation for high-stakes claims). Public
pipelines reject unregistered verifiers, and the typed spine independently checks the
claim proof rather than trusting a result's Boolean. A correct symbolic proof of the
*wrong formalization* is not a verified claim.

### `Verified` precondition summary

```
VERIFIED  <=> non-LLM definitive verifier PASS
          AND claim-binding checked (formalization_checked)
          AND (for sourced verifiers) authoritative tier AND integrity verified
          AND no definitive contradiction
          AND verifiability_class = MECHANICAL   (else capped by ceiling)
```

---

## 4. Contested as a flag and a release label

`CONTESTED` is surfaced when support and contradiction coexist. A definitive pass plus a
credible contradiction is also released as `CONTESTED`, never `VERIFIED`. Internally the
decision still carries `contested:bool` and `direction`, allowing composition and review
routing to preserve simultaneous support and dispute without relying on the label alone.

---

## 5. Status is maintained, not a snapshot (TTL / staleness)

Every positive status (`VERIFIED`/`CORROBORATED`/`SUPPORTED`) is stamped with
`decided_at` and, if the claim carries a `ttl`, an `expires_at`. Once
`now >= expires_at`, the status auto-demotes to `EXPIRED`, forcing re-entry to the
verifier plan. This makes freshness coherent *before* the full source-change
monitor exists: a `Verified` claim cannot silently rot.

---

## 6. Composition of dependent claims (weakest admissible link)

For a conclusion `C` that depends (conjunctively) on premises `A, B, ...`:

1. If **any** premise is `REFUTED` -> `C` is `REFUTED` (the conjunction is broken).
2. Else if **any** premise is `EXPIRED` -> `C` is `EXPIRED` (must re-verify).
3. Else `C`'s status is capped to the **weakest** of {`C`'s own status, all
   premise statuses} on the positive ladder
   (`VERIFIED > CORROBORATED > SUPPORTED > CONTESTED > UNVERIFIED`).
4. `contested` propagates: `C.contested = OR(all contested flags)`.

> A confident conclusion may **never** inherit more certainty than its premises
> earned. `VERIFIED AND SUPPORTED` premises => at best a `SUPPORTED` conclusion.

---

## 7. Tunable thresholds (policy, not code)

| Knob | Default | Meaning |
|---|---|---|
| `corroboration_min_lineages` | `2` | independent lineages needed for `CORROBORATED` |
| `support_min_strength` | `0.3` | min admissibility-weighted strength to count as support |
| `contradiction_min_strength` | `0.5` | min strength for a contradiction to be "credible" |
| `min_authority_for_verified` | `(AUTHORITATIVE,)` | source tiers a *sourced* definitive verifier may rely on |

These live in `StatusPolicy` and are passed in, so the spine is the same across
domains while the bar can be tuned per deployment.
