"""Stable premise identity and graph composition helpers.

MVP scope (deliberately conservative):
  * canonicalize() only lowercases + collapses whitespace. It UNDER-dedups (two
    near-identical premises may get separate ids) rather than risk MERGING genuinely
    distinct claims - the fail-safe direction. Richer normalization is deferred.
  * normalize_premises() dedups first-seen by (canonical_statement, scope).
  * compose_graph() composes weakest-link to a FIXPOINT over the whole dependency graph:
    chained premises propagate transitively (a weak leaf drags down every ancestor),
    declared-but-missing/unresolved dependencies DOWNGRADE the conclusion (never leave it
    elevated), and cycles are detected and broken (no infinite loop). Today deliberate
    emits flat PREMISE nodes with empty depends_on, so it is a no-op there; it is exercised
    directly by the frozen tests.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum

from .status import (
    Direction,
    EvidenceStatus,
    StatusDecision,
    compose,
    refresh_for_staleness,
)


class PremiseRole(Enum):
    ROOT_CLAIM = "root_claim"
    PREMISE = "premise"
    ASSUMPTION = "assumption"
    EVIDENCE_CLAIM = "evidence_claim"
    CONCLUSION = "conclusion"


@dataclass(frozen=True)
class PremiseNode:
    premise_id: str
    text: str
    canonical_statement: str
    role: PremiseRole = PremiseRole.PREMISE
    scope: str = ""
    depends_on: tuple = ()
    version_id: str = "v1"


def canonicalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def premise_id_for(canonical: str, scope: str = "") -> str:
    return "p_" + hashlib.sha256((canonical + chr(0) + scope).encode()).hexdigest()[:16]


def _optional_at(values: Sequence | None, index: int, default):
    if values is None or index >= len(values):
        return default
    return values[index] or default


def normalize_premises(texts, *, scopes=None, roles=None, deps=None) -> tuple[PremiseNode, ...]:
    seen = set()
    nodes = []
    for i, text in enumerate(texts):
        canonical = canonicalize(text)
        scope = _optional_at(scopes, i, "")
        key = (canonical, scope)
        if key in seen:
            continue
        seen.add(key)
        nodes.append(PremiseNode(
            premise_id=premise_id_for(canonical, scope),
            text=text,
            canonical_statement=canonical,
            role=_optional_at(roles, i, PremiseRole.PREMISE),
            scope=scope,
            depends_on=tuple(_optional_at(deps, i, ())),
            version_id="v1",
        ))
    return tuple(nodes)


def _downgrade(decision: StatusDecision, now, reason: str) -> StatusDecision:
    """Force a conclusion to UNVERIFIED because a declared dependency could not be
    established (missing/unresolved) or sits in a cycle. We DOWNGRADE rather than leave
    the conclusion elevated: an unestablished premise can never lend assurance upward.
    Already-non-positive decisions only gain the reason."""
    if decision.status in (EvidenceStatus.VERIFIED, EvidenceStatus.CORROBORATED,
                           EvidenceStatus.SUPPORTED, EvidenceStatus.CONTESTED):
        return replace(decision, status=EvidenceStatus.UNVERIFIED, direction=Direction.NEUTRAL,
                       reasons=(*decision.reasons, reason), decided_at=now)
    return replace(decision, reasons=(*decision.reasons, reason), decided_at=now)


def compose_graph(verifications, premise_graph, now) -> list:
    """Compose weakest-link assurance to a FIXPOINT over the dependency graph.

    Pure: returns a NEW list of (text, StatusDecision) in the same shape and order.

      * Chains propagate transitively: premises are composed before their conclusions
        (topological order), so a weak leaf drags down every ancestor that depends on it.
      * A declared dependency that is MISSING from `verifications` (or itself unresolved
        because it sits in a cycle) DOWNGRADES the conclusion to UNVERIFIED - it is never
        left elevated on the strength of a premise that was never established.
      * CYCLES are detected and broken (no infinite loop): every node on a dependency cycle
        is downgraded, since none of them has a well-founded composition.
    """
    out = list(verifications)
    by_claim_id = {decision.claim_id: i for i, (_, decision) in enumerate(out)}
    # depends_on edges, restricted to nodes that have a verification entry.
    deps = {node.premise_id: tuple(node.depends_on or ())
            for node in premise_graph if node.premise_id in by_claim_id}

    # 1) Missing dependencies: a declared dep with no verification entry means the premise
    #    was never assessed -> downgrade the dependent conclusion. Reasons are deterministic
    #    (sorted) so output is stable.
    missing_dep = {cid for cid, dep_ids in deps.items()
                   if any(dep_id not in by_claim_id for dep_id in dep_ids)}

    # 2) Cycle detection over the resolvable subgraph (iterative DFS, three-colour).
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {cid: WHITE for cid in deps}
    on_cycle: set = set()

    def _visit(start: str) -> None:
        stack = [(start, iter([d for d in deps.get(start, ()) if d in deps]))]
        colour[start] = GREY
        path = [start]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if colour[nxt] == GREY:           # back-edge -> cycle
                    if nxt in path:
                        on_cycle.update(path[path.index(nxt):])
                    else:
                        on_cycle.add(nxt)
                    on_cycle.add(node)
                elif colour[nxt] == WHITE:
                    colour[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, iter([d for d in deps.get(nxt, ()) if d in deps])))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                stack.pop()
                if path and path[-1] == node:
                    path.pop()

    for cid in deps:
        if colour[cid] == WHITE:
            _visit(cid)

    # 3) Topological order over the acyclic, resolvable subgraph (Kahn). Nodes that are on
    #    a cycle or depend on something missing/unresolved are handled by downgrade and are
    #    NOT composed (composing against an unestablished premise would understate the cause).
    #    Unresolvedness propagates UP the graph: a conclusion that depends (transitively) on
    #    an unresolved premise is itself unresolved, so it can never inherit assurance from it.
    unresolved = set(missing_dep) | set(on_cycle)
    changed = True
    while changed:
        changed = False
        for cid, dep_ids in deps.items():
            if cid in unresolved:
                continue
            if any(dep_id in unresolved for dep_id in dep_ids):
                unresolved.add(cid)
                changed = True
    resolvable = [cid for cid in deps if cid not in unresolved]
    indegree = {cid: 0 for cid in resolvable}
    children: dict = {cid: [] for cid in resolvable}
    for cid in resolvable:
        for dep_id in deps[cid]:
            if dep_id in indegree:               # dep is itself resolvable
                indegree[cid] += 1
                children[dep_id].append(cid)
    queue = [cid for cid in resolvable if indegree[cid] == 0]
    order = []
    while queue:
        cid = queue.pop(0)
        order.append(cid)
        for child in children[cid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    # 4) Compose in topological order so each conclusion sees already-composed premises.
    for cid in order:
        composed_dep_ids = [d for d in deps[cid] if d in by_claim_id]
        if not composed_dep_ids:
            # Leaf / standalone claim: there is nothing to compose, but reuse is still a boundary.
            # Without this, such a node bypassed refresh entirely and an expired leaf could be
            # handed back as VERIFIED (and released with a valid signed receipt).
            idx = by_claim_id[cid]
            text, decision = out[idx]
            out[idx] = (text, refresh_for_staleness(decision, now))
            continue
        idx = by_claim_id[cid]
        text, conclusion_decision = out[idx]
        premise_decisions = [
            out[by_claim_id[dep_id]][1] for dep_id in composed_dep_ids
        ]
        out[idx] = (text, compose(conclusion_decision, premise_decisions, now))

    # 5) Downgrade everything that could not be soundly established.
    for cid in sorted(unresolved):
        idx = by_claim_id[cid]
        text, decision = out[idx]
        if cid in on_cycle:
            reason = f"dependency cycle detected involving {cid}; cannot establish - downgraded"
        else:
            missing = sorted(d for d in deps[cid] if d not in by_claim_id)
            culprit = (
                missing[0]
                if missing
                else sorted(d for d in deps[cid] if d in unresolved)[0]
            )
            reason = (f"declared dependency {culprit} missing/unresolved; "
                      f"conclusion downgraded (cannot inherit assurance from an unestablished premise)")
        out[idx] = (text, _downgrade(decision, now, reason))

    return out
