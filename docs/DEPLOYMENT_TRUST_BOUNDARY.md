# VeriFlow Deployment Trust Boundary

## What the kernel protects

VeriFlow rejects trust claims reconstructed from ordinary objects, JSON, copied dataclasses,
caller-controlled metadata, or model output. Trusted proofs are content-bound to the exact claim or
evidence subject, and definitive status still requires the corresponding provenance and capability
checks.

## What it does not protect

Python module privacy is not process isolation. Code already executing in the VeriFlow interpreter
can call operator APIs such as:

- `issue_trusted_binding_proof` and the other `issue_trusted_*` factories;
- `issue_trusted_source_records` and `issue_trusted_companyfacts`;
- `trust_resolver`;
- capability-registry construction and registration APIs.

Such code is inside the trusted computing base. The sentinel mechanism prevents data-plane forgery;
it cannot defend against arbitrary code execution in its own process.

## Required production shape

1. Keep proof issuance, trusted resolvers, registries, release policy, and signing keys in an
   operator-controlled verifier service.
2. Run tenant code, third-party plugins, and model-generated code in separate processes or services.
3. Accept only serialized request and advisory DTOs across that boundary. Never accept a caller's
   claim that an object is trusted.
4. Configure trusted adapters and capabilities from an operator-owned allowlist at startup, then
   prevent runtime mutation by request handlers.
5. Keep stable receipt/audit signing keys in an external secret manager when verification must
   survive process restarts.
6. Treat demo and test issuance calls as stipulated fixtures, not independent semantic review.

The optional Lab Bench Stage-0 adapter follows this pattern but is not an OS sandbox: it launches
the exactly allowlisted gate source as the same OS user in a separate Python process. The parent
executes a private snapshot of bytes it has already hashed, validates the receipt and release
identity, and independently recomputes the decision. Do not add a new gate hash to the allowlist
without code review; use a stronger container or service boundary for third-party or otherwise
untrusted executable adapters.

If hostile in-process extensions are a product requirement, the next step is an out-of-process proof
authority using asymmetric signatures or an equivalent platform capability. Renaming a Python
function or hiding a sentinel would not create that security boundary.
