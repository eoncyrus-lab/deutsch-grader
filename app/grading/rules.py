from __future__ import annotations

from typing import Any

from app.config.models import Expr


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def evaluate(expr: Expr | dict | None, facts: dict[str, Any]) -> Any:
    if expr is None:
        return None
    if isinstance(expr, dict):
        expr = Expr.model_validate(expr)
    op = expr.op
    if op == "field":
        return facts.get(expr.field)
    if op == "const":
        return expr.value
    if op == "and":
        return all(bool(evaluate(arg, facts)) for arg in expr.args)
    if op == "or":
        return any(bool(evaluate(arg, facts)) for arg in expr.args)
    if op == "not":
        return not bool(evaluate(expr.args[0], facts))
    left = evaluate(expr.args[0], facts)
    right = evaluate(expr.args[1], facts)
    if op in {"eq", "ne"}:
        result = left == right
        return result if op == "eq" else not result
    a, b = _num(left), _num(right)
    if a is None or b is None:
        return False
    if op == "gt":
        return a > b
    if op == "gte":
        return a >= b
    if op == "lt":
        return a < b
    if op == "lte":
        return a <= b
    return False


def facts_from_checklist(values: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for key, raw in values.items():
        value = raw.get("value") if isinstance(raw, dict) and "value" in raw else raw
        facts[key] = value
        if isinstance(value, list):
            facts[f"{key}_count"] = len(value)
    return facts
