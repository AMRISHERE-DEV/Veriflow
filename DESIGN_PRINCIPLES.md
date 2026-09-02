# Glass Ionomer Engineering Constitution

This document is normative. It defines the architecture of this public cut and
the standard used to accept changes.

## Product objective

Glass Ionomer examines a question, separates its checkable claims, records admissible
evidence, assigns bounded evidence statuses, and decides whether the resulting
answer may be released. It must make uncertainty and dissent visible.

Glass Ionomer is not a truth oracle. A status describes what the configured evidence
and verifier proved within a recorded scope and time.

## Two flows, one boundary

The verification kernel:

```text
extract -> resolve -> admit -> verify -> decide_status
```

The release engine:

```text
propose -> certify -> enforce -> receipt
```

Adapters and demos do not implement status, release, or provenance policy.
Specialist lanes expose typed APIs, but they must enter the same verification
and release boundaries before making a user-facing assurance claim.

## Ownership boundaries

Each policy has one owner:

| Concern | Owner |
| --- | --- |
| Evidence admission and status | `glass_ionomer.verify` |
| Source resolution and resolver-owned bytes | `glass_ionomer.verify.resolver` |
| Release action and receipt | `glass_ionomer.engine.enforcer` |
| Deterministic domain mechanisms | `glass_ionomer.lanes` |

Do not duplicate these policies in entry points, adapters, or convenience
facades. A new abstraction must remove real complexity or enforce a boundary.

## Trust invariants

1. Model output never creates evidence status or source provenance.
2. `VERIFIED` and `REFUTED` require an approved deterministic verifier bound to
   the exact claim and inputs.
3. `CORROBORATED` requires independent system-resolved provenance. Caller labels
   and self-hashes do not establish independence.
4. A source lead is not evidence. Admitted evidence is derived from
   resolver-owned bytes and carries a content-bound source proof.
5. A retrieval, parsing, or binding failure lowers assurance and is disclosed.
   It never becomes negative evidence and never raises status.
6. Release is deterministic and cannot outrun the weakest material claim.
7. Receipts bind every user-visible field. A deployment signature is required
   for tamper attestation; an unsigned hash proves consistency only.
8. Raw personal information is minimised before external calls and durable
   output. Exact source hashes remain available for replay.
9. Arbitrary tenant code and model-generated code do not run inside the
   verifier process.

## Complexity rules

- Prefer explicit data flow over registries, factories, and hooks.
- Keep one public representation for each concept, especially status and release.
- Do not add a facade that merely renames or forwards another public API.
- Keep policy pure where possible and side effects at adapters.
- Bound collections, network responses, retries, concurrency, and execution time.
- Validate data at trust boundaries; keep internal code typed and direct.
- Preserve evidence and failure reasons needed for audit, but do not retain raw
  prompts, secrets, redundant intermediate objects, or decorative telemetry.
- The verification core remains standard-library only.
- Comments explain non-obvious invariants and tradeoffs, not syntax.

## Extension test

Before adding a component, answer all of these:

1. Which product requirement or trust invariant does it satisfy?
2. Why can its behavior not live in an existing owner module?
3. What failure mode does it introduce, and how does that failure abstain?
4. What resource bounds and observability does it need?
5. Which unit, integration, and adversarial tests prove the boundary?
6. Can an existing component be removed after this one is introduced?

If those answers are weak, do not add the component.

## Definition of done

A change is complete only when:

- public behavior and failure behavior are specified;
- trust-boundary and adversarial tests exist where relevant;
- the full hermetic suite passes on supported Python versions;
- documentation describes current behavior and limitations;
- no dead compatibility surface, secret, generated output, or machine-specific
  path is added to the repository.
