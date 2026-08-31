"""Shared bounded arithmetic evaluation utilities.

This module is stdlib-only and intentionally small. It supports two callers:
mechanical relation verification in ``veriflow.verify`` and variable-bound
expression evaluation in ``veriflow.spine``.
"""
from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable, Mapping
from fractions import Fraction

# DoS bounds. Canonical arithmetic relations and computation specs are small
# fixed-shape objects; oversized inputs are rejected before evaluation.
_MAX_NODES = 200
_MAX_DEPTH = 25
_MAX_ABS_OPERAND: float = 1e15
_MAX_POW_EXPONENT: int = 64

Number = int | float | Fraction
BinaryOperator = Callable[[Number, Number], Number]
UnaryOperator = Callable[[Number], Number]


def _positive(value: Number) -> Number:
    return value


def _negative(value: Number) -> Number:
    return -value


def _magnitude_exceeds(value: Number, limit: int | float) -> bool:
    if isinstance(value, Fraction):
        return abs(value) > Fraction(str(limit))
    return abs(value) > limit

class ArithmeticBoundError(ValueError):
    """Raised when an arithmetic expression exceeds safety bounds."""


_RELATION_BINOPS: dict[type[ast.operator], BinaryOperator] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

_EXPRESSION_BINOPS: dict[type[ast.operator], BinaryOperator] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}

_UNARY_OPS: dict[type[ast.unaryop], UnaryOperator] = {
    ast.UAdd: _positive,
    ast.USub: _negative,
}


def check_arithmetic_bounds(tree: ast.AST) -> None:
    """Reject an arithmetic AST that exceeds the shared node/depth limits."""
    nodes = 0

    def walk(node: ast.AST, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES:
            raise ArithmeticBoundError(f"relation too large (> {_MAX_NODES} AST nodes)")
        if depth > _MAX_DEPTH:
            raise ArithmeticBoundError(f"relation too deeply nested (> {_MAX_DEPTH})")
        for child in ast.iter_child_nodes(node):
            walk(child, depth + 1)

    walk(tree, 0)


def _bound_operand(value: Number) -> Number:
    if isinstance(value, bool):
        raise ArithmeticBoundError("boolean is not a numeric operand")
    if isinstance(value, float) and not math.isfinite(value):
        raise ArithmeticBoundError("non-finite operand (inf/nan) rejected")
    if _magnitude_exceeds(value, _MAX_ABS_OPERAND):
        raise ArithmeticBoundError(f"operand magnitude exceeds cap ({_MAX_ABS_OPERAND:g})")
    return value


def coerce_float(value: object, *, label: str = "value") -> float:
    """Return a bounded numeric value as float; reject bool/non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"non_numeric_variable:{label}")
    return float(_bound_operand(value))


def _numeric(
    node: ast.AST,
    *,
    variables: Mapping[str, int | float] | None,
    binops: Mapping[type[ast.operator], BinaryOperator],
    as_float: bool,
) -> Number:
    if isinstance(node, ast.Expression):
        return _numeric(node.body, variables=variables, binops=binops, as_float=as_float)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        value = _bound_operand(node.value)
        if as_float:
            return float(value)
        # Relation literals use exact decimal rationals. This preserves ordinary
        # decimal identities such as 0.1 + 0.2 == 0.3 without a magnitude-scaled
        # tolerance that can collapse materially distinct large values.
        return Fraction(str(value)) if isinstance(value, float) else value

    if isinstance(node, ast.Name):
        if variables is None or node.id not in variables:
            raise ValueError(f"unknown_variable:{node.id}")
        value = coerce_float(variables[node.id], label=node.id)
        return value if as_float else Fraction(str(_bound_operand(value)))

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        value = _numeric(node.operand, variables=variables, binops=binops, as_float=as_float)
        result = _bound_operand(_UNARY_OPS[type(node.op)](value))
        return float(result) if as_float else result

    if isinstance(node, ast.BinOp) and type(node.op) in binops:
        left = _numeric(node.left, variables=variables, binops=binops, as_float=as_float)
        right = _numeric(node.right, variables=variables, binops=binops, as_float=as_float)
        if isinstance(node.op, ast.Pow) and _magnitude_exceeds(
            right, _MAX_POW_EXPONENT
        ):
            raise ArithmeticBoundError(f"exponent magnitude exceeds cap ({_MAX_POW_EXPONENT})")
        result = _bound_operand(binops[type(node.op)](left, right))
        return float(result) if as_float else result

    raise ValueError(f"unsafe_syntax:{type(node).__name__}")


def compare(op: ast.AST, left: Number, right: Number) -> bool:
    """Compare two bounded numeric operands.

    Relation comparison is exact. ``evaluate_relation`` converts decimal literals
    to exact rationals before evaluation; direct float callers retain Python's exact
    float comparison. Approximate equality belongs in a typed domain policy, never
    in the canonical mechanical-status gate.
    """
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.GtE):
        return left >= right
    raise ValueError(f"unsupported comparison: {type(op).__name__}")


def evaluate_relation(canonical: str) -> bool:
    """Evaluate a bounded arithmetic comparison, including chained relations."""
    tree = ast.parse(canonical, mode="eval")
    check_arithmetic_bounds(tree)
    comp = tree.body
    if not isinstance(comp, ast.Compare):
        raise ValueError("not a comparison")

    left = _numeric(comp.left, variables=None, binops=_RELATION_BINOPS, as_float=False)
    ok = True
    for op, right_node in zip(comp.ops, comp.comparators, strict=True):
        right = _numeric(right_node, variables=None, binops=_RELATION_BINOPS, as_float=False)
        ok = ok and compare(op, left, right)
        left = right
    return ok


def evaluate_numeric_expression(expression: str, variables: Mapping[str, int | float]) -> float:
    """Evaluate a bounded arithmetic expression over supplied numeric variables."""
    tree = ast.parse(expression, mode="eval")
    check_arithmetic_bounds(tree)
    return float(_numeric(tree, variables=variables, binops=_EXPRESSION_BINOPS, as_float=True))
