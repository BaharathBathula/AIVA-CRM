"""A small, safe expression language for formula fields and button functions.

Examples::

    amount * 0.1
    IF(priority == "high", "Call today", "Email this week")
    ROUND(premium / 12, 2)
    DAYS_BETWEEN(TODAY(), expected_close_date)
    CONCAT(first_name, " ", last_name)

Only arithmetic, comparisons, ``and``/``or``/``not``, conditional
expressions and the functions below are allowed. Names refer to fields of
the record. Nothing else in Python is reachable.
"""

from __future__ import annotations

import ast
import operator
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

MAX_LENGTH = 500


class ExpressionError(ValueError):
    pass


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _days_between(a: Any, b: Any) -> int | None:
    first, second = _as_date(a), _as_date(b)
    if first is None or second is None:
        return None
    return (second - first).days


def _round(value: Any, digits: Any = 0) -> float | None:
    number = _num(value)
    return None if number is None else round(number, int(_num(digits) or 0))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "IF": lambda condition, yes=None, no=None: yes if condition else no,
    "ROUND": _round,
    "MIN": lambda *values: min((n for n in map(_num, values) if n is not None), default=None),
    "MAX": lambda *values: max((n for n in map(_num, values) if n is not None), default=None),
    "ABS": lambda value: None if _num(value) is None else abs(_num(value)),
    "LEN": lambda value: len(_text(value)),
    "UPPER": lambda value: _text(value).upper(),
    "LOWER": lambda value: _text(value).lower(),
    "CONCAT": lambda *values: "".join(_text(v) for v in values),
    "CONTAINS": lambda text, part: _text(part).lower() in _text(text).lower(),
    "TODAY": lambda: date.today(),
    "NOW": lambda: datetime.now(timezone.utc),
    "DAYS_BETWEEN": _days_between,
    "COALESCE": lambda *values: next((v for v in values if v not in (None, "")), None),
    "NUMBER": _num,
    "TEXT": _text,
}

BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}
COMPARE_OPS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def parse(expression: str) -> ast.Expression:
    text = (expression or "").strip()
    if not text:
        raise ExpressionError("Enter an expression.")
    if len(text) > MAX_LENGTH:
        raise ExpressionError(f"Keep expressions under {MAX_LENGTH} characters.")
    # Friendlier spelling for people used to spreadsheets.
    text = text.replace("<>", "!=").replace(" AND ", " and ").replace(" OR ", " or ").replace("NOT ", "not ")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Check the expression near character {exc.offset or 0}.") from exc
    _check(tree.body)
    return tree


def _check(node: ast.AST) -> None:
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, str, bool, type(None))):
            raise ExpressionError("Only numbers, text, true/false and None are allowed.")
        return
    if isinstance(node, ast.Name):
        return
    if isinstance(node, ast.BinOp):
        if type(node.op) not in BIN_OPS:
            raise ExpressionError("Use + - * / or % for arithmetic.")
        _check(node.left)
        _check(node.right)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            raise ExpressionError("That operator isn't supported.")
        _check(node.operand)
        return
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            _check(value)
        return
    if isinstance(node, ast.Compare):
        if any(type(op) not in COMPARE_OPS for op in node.ops):
            raise ExpressionError("Use ==, !=, <, <=, > or >= to compare.")
        _check(node.left)
        for comparator in node.comparators:
            _check(comparator)
        return
    if isinstance(node, ast.IfExp):
        _check(node.test)
        _check(node.body)
        _check(node.orelse)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id.upper() not in FUNCTIONS:
            raise ExpressionError(f"Unknown function. Available: {', '.join(sorted(FUNCTIONS))}.")
        if node.keywords:
            raise ExpressionError("Pass function arguments in order, without names.")
        for arg in node.args:
            _check(arg)
        return
    raise ExpressionError("That isn't allowed in an expression.")


def _eval(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ("true", "True"):
            return True
        if node.id in ("false", "False"):
            return False
        if node.id in ("null", "None"):
            return None
        return names.get(node.id)
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, names), _eval(node.right, names)
        if isinstance(node.op, ast.Add) and (isinstance(left, str) or isinstance(right, str)):
            return _text(left) + _text(right)
        a, b = _num(left), _num(right)
        if a is None or b is None:
            return None
        if isinstance(node.op, (ast.Div, ast.Mod)) and b == 0:
            return None
        return BIN_OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, names)
        if isinstance(node.op, ast.Not):
            return not value
        number = _num(value)
        if number is None:
            return None
        return -number if isinstance(node.op, ast.USub) else number
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, names) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, names)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, names)
            a, b = left, right
            if _num(a) is not None and _num(b) is not None and not isinstance(a, str) and not isinstance(b, str):
                a, b = _num(a), _num(b)
            elif _as_date(a) and _as_date(b) and (isinstance(a, (date, datetime)) or isinstance(b, (date, datetime))):
                a, b = _as_date(a), _as_date(b)
            elif isinstance(a, str) or isinstance(b, str):
                a, b = _text(a).lower(), _text(b).lower()
            try:
                if not COMPARE_OPS[type(op)](a, b):
                    return False
            except TypeError:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, names) if _eval(node.test, names) else _eval(node.orelse, names)
    if isinstance(node, ast.Call):
        function = FUNCTIONS[node.func.id.upper()]
        try:
            return function(*[_eval(arg, names) for arg in node.args])
        except TypeError as exc:
            raise ExpressionError(f"{node.func.id.upper()} got the wrong number of values.") from exc
    raise ExpressionError("That isn't allowed in an expression.")


def evaluate(expression: str | ast.Expression, names: dict[str, Any]) -> Any:
    tree = parse(expression) if isinstance(expression, str) else expression
    try:
        return _eval(tree.body, names)
    except ExpressionError:
        raise
    except (ArithmeticError, ValueError, OverflowError) as exc:
        raise ExpressionError(str(exc)) from exc


def names_in(expression: str) -> set[str]:
    """Field names an expression reads (function names excluded)."""

    tree = parse(expression)
    function_nodes = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and id(node) not in function_nodes
        and node.id not in {"true", "True", "false", "False", "null", "None"}
    }
