"""
capabilities/computation.py — Mathematical and Computational capabilities.

Covers NIKKI capability family: 29 (COMPUTATION & MATHEMATICS)

calc.evaluate uses an AST-whitelist arithmetic evaluator — never eval().
Only numeric literals and + - * / // % ** (with unary +/-) are accepted.
"""
from __future__ import annotations

import ast
import math
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap

# Guard rails for the arithmetic evaluator.
_MAX_NODES = 500
_MAX_DEPTH = 64
_MAX_OPERAND_DIGITS = 32  # per literal, rejects absurd huge-number DoS
_MAX_EXPONENT = 10_000  # rejects 10**10**10-style blowups

_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}
_UNARY_OPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


class _CalcError(ValueError):
    """Raised for any expression the whitelist evaluator rejects."""


def _eval_node(node: ast.AST, depth: int = 0) -> float | int:
    if depth > _MAX_DEPTH:
        raise _CalcError("expression nesting too deep")
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, depth + 1)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _CalcError("only numeric literals are allowed")
        value = node.value
        if isinstance(value, int) and len(str(abs(value))) > _MAX_OPERAND_DIGITS:
            raise _CalcError("numeric literal too large")
        return value
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise _CalcError(f"operator {op_type.__name__} not allowed")
        left = _eval_node(node.left, depth + 1)
        right = _eval_node(node.right, depth + 1)
        if op_type is ast.Pow and abs(right) > _MAX_EXPONENT:
            raise _CalcError("exponent too large")
        try:
            return _BIN_OPS[op_type](left, right)
        except ZeroDivisionError:
            raise _CalcError("division by zero")
        except OverflowError:
            raise _CalcError("numeric overflow")
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise _CalcError(f"unary operator {op_type.__name__} not allowed")
        operand = _eval_node(node.operand, depth + 1)
        return _UNARY_OPS[op_type](operand)
    raise _CalcError(f"syntax element {type(node).__name__} not allowed")


def evaluate_expression(expression: str) -> float | int:
    """Safely evaluate a pure-arithmetic expression. No eval(), no names."""
    if not isinstance(expression, str) or not expression.strip():
        raise _CalcError("expression is required")
    if len(expression) > 2_000:
        raise _CalcError("expression too long")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise _CalcError(f"invalid expression syntax: {exc.msg}")
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > _MAX_NODES:
        raise _CalcError("expression too complex")
    result = _eval_node(tree)
    if isinstance(result, float) and math.isfinite(result):
        # Avoid float noise like 0.30000000000000004 for display purposes.
        rounded = round(result, 10)
        if rounded == int(rounded):
            return int(rounded)
        return rounded
    return result


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _calc_evaluate(args: dict[str, Any], state: Any = None) -> Any:
        expr = args.get("expression", "")
        try:
            value = evaluate_expression(str(expr))
            return ok({"expression": expr, "result": value})
        except _CalcError as e:
            return fail(str(e))
        except Exception as e:
            return fail(f"Math evaluation error: {e}")

    register_cap(registry, Cap("calc.evaluate", "Safely evaluate a pure arithmetic expression (+ - * / // % ** with parentheses). No eval().", Cap.READ, ("expression",)), _calc_evaluate)

    def _calc_percentage(args: dict[str, Any], state: Any = None) -> Any:
        part = args.get("part")
        whole = args.get("whole")
        if part is None or whole is None:
            return fail("part and whole are required")
        try:
            part_v = float(part)
            whole_v = float(whole)
        except (TypeError, ValueError):
            return fail("part and whole must be numbers")
        if whole_v == 0:
            return fail("whole cannot be zero")
        return ok({"percentage": round(part_v / whole_v * 100, 10)})

    register_cap(registry, Cap("calc.percentage", "Calculate what percentage part is of whole", Cap.READ, ("part", "whole")), _calc_percentage)

    def _calc_unit_convert(args: dict[str, Any], state: Any = None) -> Any:
        value = args.get("value")
        unit = str(args.get("unit", "")).lower()
        conversions = {
            "km_to_mi": 0.621371,
            "mi_to_km": 1.609344,
            "kg_to_lb": 2.204623,
            "lb_to_kg": 0.453592,
            "c_to_f": None,
            "f_to_c": None,
            "m_to_ft": 3.28084,
            "ft_to_m": 0.3048,
        }
        if unit not in conversions:
            return fail(f"unknown unit conversion {unit!r}; supported: {sorted(conversions)}")
        try:
            v = float(value)
        except (TypeError, ValueError):
            return fail("value must be a number")
        if unit == "c_to_f":
            return ok({"value": round(v * 9 / 5 + 32, 10), "unit": "fahrenheit"})
        if unit == "f_to_c":
            return ok({"value": round((v - 32) * 5 / 9, 10), "unit": "celsius"})
        factor = conversions[unit]
        return ok({"value": round(v * factor, 10), "unit": unit.split("_to_")[-1]})

    register_cap(registry, Cap("calc.unit_convert", "Convert common units (km/mi, kg/lb, c/f, m/ft)", Cap.READ, ("value", "unit")), _calc_unit_convert)
