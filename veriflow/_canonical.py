"""Strict, type-preserving canonical serialization for trust and replay digests."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented in the canonical digest domain."""


_MAX_DEPTH = 600
_MAX_NODES = 100_000
_MAX_CONFIGURED_NODES = 1_000_000


def _normalize(
    value: Any,
    *,
    depth: int,
    seen: set[int],
    nodes: list[int],
    max_nodes: int,
):
    nodes[0] += 1
    if nodes[0] > max_nodes:
        raise CanonicalizationError("canonical value exceeds node limit")
    if depth > _MAX_DEPTH:
        raise CanonicalizationError("canonical value exceeds depth limit")

    if value is None:
        return ["null", None]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("non-finite floats are not canonical")
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]

    if isinstance(value, (list, tuple, dict)):
        identity = id(value)
        if identity in seen:
            raise CanonicalizationError("cyclic values are not canonical")
        seen.add(identity)
        try:
            if isinstance(value, list):
                return ["list", [_normalize(
                    v, depth=depth + 1, seen=seen, nodes=nodes, max_nodes=max_nodes)
                                 for v in value]]
            if isinstance(value, tuple):
                return ["tuple", [_normalize(
                    v, depth=depth + 1, seen=seen, nodes=nodes, max_nodes=max_nodes)
                                  for v in value]]
            if not all(isinstance(key, str) for key in value):
                raise CanonicalizationError("canonical dictionaries require string keys")
            return ["dict", [
                [key, _normalize(
                    value[key], depth=depth + 1, seen=seen, nodes=nodes,
                    max_nodes=max_nodes)]
                for key in sorted(value)
            ]]
        finally:
            seen.remove(identity)

    raise CanonicalizationError(
        f"unsupported canonical type: {type(value).__module__}.{type(value).__qualname__}")


def canonical_dumps(value: Any, *, max_nodes: int = _MAX_NODES) -> str:
    """Serialize supported values with explicit type/domain separation.

    The conservative default protects ordinary trust-boundary inputs. Approved
    bounded document adapters may request a larger, still-capped node budget
    when their transport already enforces a byte limit.
    """
    if (isinstance(max_nodes, bool) or not isinstance(max_nodes, int)
            or not 1 <= max_nodes <= _MAX_CONFIGURED_NODES):
        raise ValueError(
            f"max_nodes must be an integer within 1..{_MAX_CONFIGURED_NODES}")
    normalized = _normalize(
        value, depth=0, seen=set(), nodes=[0], max_nodes=max_nodes)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_sha256(value: Any, *, max_nodes: int = _MAX_NODES) -> str:
    return hashlib.sha256(
        canonical_dumps(value, max_nodes=max_nodes).encode("utf-8")
    ).hexdigest()
