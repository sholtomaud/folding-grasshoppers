"""Formulas: deterministic quote lines and other derived numbers.

Claude never does arithmetic. Every number in a quote is a **formula** over
**fields** (settings and measurements of the design), evaluated here::

    material = round(sheets * sheet_price, 2)          AUD
    install_hours = ceil(parts / 40)                   h

The language is small and safe: numbers, names, ``+ - * / ^``, comparisons,
``and or not``, and the functions ``min max round ceil floor abs if sum count
avg``. There is no Python ``eval``, no loops, no strings, and no way to reach
files or the network: anything outside the grammar is rejected when parsed.

Units are checked: ``cut_minutes + board_area`` is an error, and a result must
match the unit the formula declares (a plain number takes the declared unit,
so ``ceil(parts / 40)`` can be hours). Units are written like ``mm``, ``mm2``,
``min``, ``h``, ``AUD``, ``AUD/sheet``, ``AUD/h``, ``mm/s``, ``%``.

Every formula carries a worked example (inputs and the expected result) that
is checked before it is saved, and a version (a hash of its expression and
unit) that each quote records, so an old quote can always be reproduced.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

FORMULAS_SCHEMA = "cardboardcad.formulas/1"
FORMULAS_FILE = "formulas.json"
MAX_EXPR = 500
MAX_TOKENS = 200
MAX_DEPTH = 40
MAX_FORMULAS = 200
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
ROLES = ("line", "total", "subtotal", "working")


class FormulaError(ValueError):
    """A formula that cannot be parsed, checked or evaluated. The message says why."""


# ---- units ------------------------------------------------------------------
# A unit is (dims, scale): dims are powers of (length, time, money); scale turns
# a value in this unit into base units (mm, s, one unit of currency).
Dims = Tuple[int, int, int]
NONE: Dims = (0, 0, 0)

_UNIT_NAMES: Dict[str, Tuple[Dims, float]] = {
    "mm": ((1, 0, 0), 1.0), "cm": ((1, 0, 0), 10.0), "m": ((1, 0, 0), 1000.0),
    "s": ((0, 1, 0), 1.0), "sec": ((0, 1, 0), 1.0), "min": ((0, 1, 0), 60.0),
    "h": ((0, 1, 0), 3600.0), "hr": ((0, 1, 0), 3600.0), "day": ((0, 1, 0), 86400.0),
    "days": ((0, 1, 0), 86400.0), "$": ((0, 0, 1), 1.0), "%": (NONE, 0.01),
}
_COUNT_WORDS = {"count", "each", "x", "fraction", "sheet", "sheets", "part", "parts", "pierce", "pierces",
                "joint", "joints", "item", "items", "person", "people"}


def _unit_term(term: str) -> Tuple[Dims, float]:
    m = re.fullmatch(r"([A-Za-z$%]+)(?:\^?([0-9])|([²³]))?", term)
    if not m:
        raise FormulaError(f"Unknown unit {term!r}")
    name, power = m.group(1), m.group(2) or {"²": "2", "³": "3"}.get(m.group(3) or "", "1")
    if name in _UNIT_NAMES:
        dims, scale = _UNIT_NAMES[name]
    elif name in _COUNT_WORDS:
        dims, scale = NONE, 1.0
    elif re.fullmatch(r"[A-Z]{3}", name):  # a currency code: AUD, NZD, USD...
        dims, scale = (0, 0, 1), 1.0
    else:
        raise FormulaError(f"Unknown unit {name!r}. Use mm, m, mm2, s, min, h, a currency such as AUD, "
                           "count, sheet, % or combinations such as AUD/h")
    p = int(power)
    return tuple(d * p for d in dims), scale ** p  # type: ignore[return-value]


def parse_unit(unit) -> Tuple[Dims, float]:
    """``"AUD/h"`` -> ((0, -1, 1), 1/3600). An empty unit is a plain number."""
    if unit is None:
        return NONE, 1.0
    if not isinstance(unit, str) or len(unit) > 40:
        raise FormulaError("A unit must be short text such as mm, min or AUD/h")
    text = unit.strip().replace("·", "*").replace(" ", "")
    if not text:
        return NONE, 1.0
    dims, scale = [0, 0, 0], 1.0
    sign = 1
    for part in re.split(r"([*/])", text):
        if part == "*":
            sign = 1
        elif part == "/":
            sign = -1
        elif part:
            d, s = _unit_term(part)
            for i in range(3):
                dims[i] += sign * d[i]
            scale = scale * s if sign > 0 else scale / s
        else:
            raise FormulaError(f"Cannot read the unit {unit!r}")
    return tuple(dims), scale  # type: ignore[return-value]


def _dims_text(d: Dims) -> str:
    if d == NONE:
        return "a plain number"
    names = ("mm", "s", "money")
    num = [f"{n}{'' if p == 1 else p}" for n, p in zip(names, d) if p > 0]
    den = [f"{n}{'' if p == -1 else -p}" for n, p in zip(names, d) if p < 0]
    return (" ".join(num) or "1") + (" per " + " ".join(den) if den else "")


# ---- parsing ----------------------------------------------------------------
FUNCTIONS = {"min", "max", "round", "ceil", "floor", "abs", "if", "sum", "count", "avg"}
KEYWORDS = {"and", "or", "not"}
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*)
  | (?P<op><=|>=|==|!=|[-+*/^<>(),])
""", re.VERBOSE)


def _tokens(expr: str) -> List[Tuple[str, str]]:
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError("A formula needs an expression, for example sheets * sheet_price")
    if len(expr) > MAX_EXPR:
        raise FormulaError(f"A formula can be at most {MAX_EXPR} characters")
    out, pos = [], 0
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m:
            raise FormulaError(f"Unexpected {expr[pos]!r} at position {pos + 1} in {expr!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        out.append((kind, m.group()))
    if len(out) > MAX_TOKENS:
        raise FormulaError("That formula is too long")
    return out


class _Parser:
    def __init__(self, expr: str):
        self.expr = expr
        self.toks = _tokens(expr)
        self.i = 0
        self.depth = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if tok[0] is None or (value is not None and tok[1] != value):
            want = f"{value!r}" if value else "more"
            got = f"{tok[1]!r}" if tok[0] else "the end"
            raise FormulaError(f"Expected {want} but found {got} in {self.expr!r}")
        self.i += 1
        return tok

    def parse(self):
        node = self.or_()
        if self.peek()[0] is not None:
            raise FormulaError(f"Unexpected {self.peek()[1]!r} in {self.expr!r}")
        return node

    def _nest(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError("That formula is nested too deeply")

    def or_(self):
        node = self.and_()
        while self.peek() == ("name", "or"):
            self.take()
            node = ("bin", "or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.peek() == ("name", "and"):
            self.take()
            node = ("bin", "and", node, self.not_())
        return node

    def not_(self):
        if self.peek() == ("name", "not"):
            self.take()
            self._nest()
            node = ("not", self.not_())
            self.depth -= 1
            return node
        return self.cmp()

    def cmp(self):
        node = self.sum()
        if self.peek()[1] in ("<", ">", "<=", ">=", "==", "!="):
            op = self.take()[1]
            node = ("bin", op, node, self.sum())
        return node

    def sum(self):
        node = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek()[1] in ("-", "+"):
            op = self.take()[1]
            self._nest()
            inner = self.unary()
            self.depth -= 1
            return ("neg", inner) if op == "-" else inner
        return self.power()

    def power(self):
        node = self.atom()
        if self.peek()[1] == "^":
            self.take()
            self._nest()
            node = ("bin", "^", node, self.unary())
            self.depth -= 1
        return node

    def atom(self):
        kind, value = self.peek()
        if kind == "num":
            self.take()
            return ("num", float(value))
        if kind == "name":
            if value in KEYWORDS:
                raise FormulaError(f"Unexpected {value!r} in {self.expr!r}")
            self.take()
            if self.peek()[1] == "(":
                if value not in FUNCTIONS:
                    raise FormulaError(f"Unknown function {value!r}. Functions: {', '.join(sorted(FUNCTIONS))}")
                self.take("(")
                self._nest()
                args = []
                if self.peek()[1] != ")":
                    args.append(self.or_())
                    while self.peek()[1] == ",":
                        self.take()
                        args.append(self.or_())
                self.take(")")
                self.depth -= 1
                return ("call", value, args)
            if value in FUNCTIONS:
                raise FormulaError(f"{value} is a function: write {value}(...)")
            return ("name", value)
        if value == "(":
            self.take()
            self._nest()
            node = self.or_()
            self.take(")")
            self.depth -= 1
            return node
        got = repr(value) if kind else "the end"
        raise FormulaError(f"Expected a number, a name or ( but found {got} in {self.expr!r}")


def parse(expr: str):
    """The expression's syntax tree. Raises FormulaError for anything outside the grammar."""
    return _Parser(expr).parse()


def names_in(node) -> List[str]:
    out: List[str] = []

    def walk(n):
        if n[0] == "name":
            if n[1] not in out:
                out.append(n[1])
        elif n[0] == "bin":
            walk(n[2])
            walk(n[3])
        elif n[0] in ("neg", "not"):
            walk(n[1])
        elif n[0] == "call":
            for a in n[2]:
                walk(a)

    walk(node)
    return out


# ---- evaluation -------------------------------------------------------------
class Q:
    """A quantity in base units: a number or a list of numbers (one per part or sheet)."""

    __slots__ = ("v", "d")

    def __init__(self, v, d: Dims = NONE):
        self.v = v
        self.d = d

    @property
    def is_list(self) -> bool:
        return isinstance(self.v, list)


def _map2(a: Q, b: Q, fn) -> Any:
    if a.is_list and b.is_list:
        if len(a.v) != len(b.v):
            raise FormulaError("Cannot combine two lists of different lengths")
        return [fn(x, y) for x, y in zip(a.v, b.v)]
    if a.is_list:
        return [fn(x, b.v) for x in a.v]
    if b.is_list:
        return [fn(a.v, y) for y in b.v]
    return fn(a.v, b.v)


def _scalar(q: Q, what: str) -> float:
    if q.is_list:
        raise FormulaError(f"{what} needs a single number, not a list: use sum(), count(), min() or max()")
    return q.v


def round_half_up(x: float, digits: int = 0) -> float:
    q = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP))


def _div(x, y):
    if y == 0:
        raise FormulaError("Division by zero")
    return x / y


def _eval(node, env: Dict[str, Q]) -> Q:
    kind = node[0]
    if kind == "num":
        return Q(node[1])
    if kind == "name":
        if node[1] not in env:
            close = [n for n in env if n.split(".")[-1] == node[1].split(".")[-1]][:3]
            hint = f" Did you mean {', '.join(close)}?" if close else ""
            raise FormulaError(f"Unknown name {node[1]!r}.{hint}")
        return env[node[1]]
    if kind == "neg":
        a = _eval(node[1], env)
        return Q([-x for x in a.v] if a.is_list else -a.v, a.d)
    if kind == "not":
        a = _eval(node[1], env)
        if a.d != NONE:
            raise FormulaError("not needs a yes/no value")
        return Q(0.0 if _scalar(a, "not") else 1.0)
    if kind == "bin":
        op = node[1]
        a = _eval(node[2], env)
        if op in ("and", "or"):
            av = _scalar(a, op)
            if (op == "and" and not av) or (op == "or" and av):
                return Q(1.0 if av else 0.0)
            b = _eval(node[3], env)
            return Q(1.0 if _scalar(b, op) else 0.0)
        b = _eval(node[3], env)
        if op in ("+", "-"):
            if a.d != b.d:
                raise FormulaError(f"Unit mismatch: cannot {'add' if op == '+' else 'subtract'} "
                                   f"{_dims_text(b.d)} {'to' if op == '+' else 'from'} {_dims_text(a.d)}")
            return Q(_map2(a, b, (lambda x, y: x + y) if op == "+" else (lambda x, y: x - y)), a.d)
        if op == "*":
            return Q(_map2(a, b, lambda x, y: x * y), tuple(x + y for x, y in zip(a.d, b.d)))
        if op == "/":
            return Q(_map2(a, b, _div), tuple(x - y for x, y in zip(a.d, b.d)))
        if op == "^":
            if b.d != NONE or b.is_list:
                raise FormulaError("A power must be a plain number")
            if a.d != NONE and not float(b.v).is_integer():
                raise FormulaError("A quantity with a unit can only be raised to a whole power")
            try:
                v = _map2(a, b, lambda x, y: float(x) ** y)
            except (OverflowError, ZeroDivisionError, ValueError):
                raise FormulaError("That power cannot be worked out") from None
            return Q(v, tuple(int(x * b.v) for x in a.d))
        # comparisons
        if a.d != b.d:
            raise FormulaError(f"Unit mismatch: cannot compare {_dims_text(a.d)} with {_dims_text(b.d)}")
        x, y = _scalar(a, op), _scalar(b, op)
        res = {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y, "==": x == y, "!=": x != y}[op]
        return Q(1.0 if res else 0.0)
    if kind == "call":
        return _call(node[1], node[2], env)
    raise FormulaError("Unknown expression")  # pragma: no cover


def _call(fn: str, args, env) -> Q:
    def need(n_min, n_max=None):
        n_max = n_min if n_max is None else n_max
        if not n_min <= len(args) <= n_max:
            want = str(n_min) if n_min == n_max else f"{n_min} to {n_max}"
            raise FormulaError(f"{fn}() takes {want} argument(s)")

    if fn == "if":
        need(3)
        c = _eval(args[0], env)
        if c.d != NONE:
            raise FormulaError("if() needs a yes/no condition first, such as parts > 40")
        return _eval(args[1] if _scalar(c, "if") else args[2], env)
    vals = [_eval(a, env) for a in args]
    if fn in ("sum", "count", "avg"):
        need(1)
        q = vals[0]
        items = q.v if q.is_list else [q.v]
        if fn == "count":
            return Q(float(len(items)))
        if fn == "avg":
            if not items:
                raise FormulaError("avg() of an empty list")
            return Q(sum(items) / len(items), q.d)
        return Q(float(sum(items)), q.d)
    if fn in ("min", "max"):
        if len(vals) == 1 and vals[0].is_list:
            if not vals[0].v:
                raise FormulaError(f"{fn}() of an empty list")
            return Q((min if fn == "min" else max)(vals[0].v), vals[0].d)
        if len(vals) < 2:
            raise FormulaError(f"{fn}() needs two or more numbers, or one list")
        d = vals[0].d
        if any(v.d != d for v in vals):
            raise FormulaError(f"Unit mismatch in {fn}(): every value needs the same unit")
        return Q((min if fn == "min" else max)(_scalar(v, fn) for v in vals), d)
    if fn == "abs":
        need(1)
        q = vals[0]
        return Q([abs(x) for x in q.v] if q.is_list else abs(q.v), q.d)
    # round, ceil, floor: plain numbers and money only (rounding 90 s to minutes needs a unit choice)
    need(1, 2 if fn == "round" else 1)
    q = vals[0]
    if q.d not in (NONE, (0, 0, 1)):
        raise FormulaError(f"{fn}() works on plain numbers and money; divide by a rate first "
                           f"(it got {_dims_text(q.d)})")
    x = _scalar(q, fn)
    if fn == "round":
        digits = 0
        if len(vals) == 2:
            if vals[1].d != NONE or not float(_scalar(vals[1], "round")).is_integer():
                raise FormulaError("round(x, digits) needs a whole number of digits")
            digits = int(vals[1].v)
            if not 0 <= digits <= 9:
                raise FormulaError("round() keeps 0 to 9 digits")
        return Q(round_half_up(x, digits), q.d)
    return Q(float(math.ceil(x) if fn == "ceil" else math.floor(x)), q.d)


def _quantity(name: str, field) -> Q:
    """A field {value, unit} (or a bare number) as a quantity in base units."""
    if isinstance(field, dict):
        value, unit = field.get("value"), field.get("unit", "")
    else:
        value, unit = field, ""
    dims, scale = parse_unit(unit)
    if isinstance(value, list):
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value):
            raise FormulaError(f"{name} must be a list of numbers")
        return Q([float(x) * scale for x in value], dims)
    if value is None:
        raise FormulaError(f"{name} has no value yet")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise FormulaError(f"{name} must be a number")
    return Q(float(value) * scale, dims)


def _result(q: Q, unit: str, what: str) -> float:
    if q.is_list:
        raise FormulaError(f"{what} gives a list, not one number: use sum(), count(), min() or max()")
    dims, scale = parse_unit(unit)
    if q.d == NONE and dims != NONE:
        value = q.v  # a plain number takes the declared unit: ceil(parts / 40) hours
    elif q.d != dims:
        raise FormulaError(f"Unit mismatch: {what} gives {_dims_text(q.d)}, but its unit is {unit!r} "
                           f"({_dims_text(dims)})")
    else:
        value = q.v / scale
    if not math.isfinite(value):
        raise FormulaError(f"{what} is not a finite number")
    return round(value, 9) + 0.0


def evaluate(expr: str, fields: Dict[str, Any], unit: str = "") -> float:
    """Evaluate one expression over fields {name: {value, unit}}; the result is in ``unit``."""
    env = {}
    tree = parse(expr)
    for name in names_in(tree):
        if name in fields:
            env[name] = _quantity(name, fields[name])
    return _result(_eval(tree, env), unit, repr(expr))


# ---- formulas ---------------------------------------------------------------
def formula_version(f: dict) -> str:
    """A short hash of what makes the formula compute what it does."""
    canon = json.dumps({"expr": " ".join(t[1] for t in _tokens(f["expr"])), "unit": (f.get("unit") or "").strip()},
                       sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def _example_fields(f: dict, units: Optional[Dict[str, str]]) -> Dict[str, Any]:
    ex = f.get("example")
    if not isinstance(ex, dict) or not isinstance(ex.get("inputs"), dict) or "expected" not in ex:
        raise FormulaError(f"Formula {f.get('key')} needs a worked example: "
                           "{\"inputs\": {name: value}, \"expected\": result}")
    out = {}
    for name, v in ex["inputs"].items():
        if isinstance(v, dict):
            out[name] = v
        else:
            out[name] = {"value": v, "unit": (units or {}).get(name, "")}
    return out


def check_example(f: dict, units: Optional[Dict[str, str]] = None) -> float:
    """Run the formula's worked example. Raises FormulaError unless it gives the expected result."""
    inputs = _example_fields(f, units)
    tree = parse(f["expr"])
    missing = [n for n in names_in(tree) if n not in inputs]
    if missing:
        raise FormulaError(f"The worked example for {f['key']} needs a value for {', '.join(missing)}")
    got = evaluate(f["expr"], inputs, f.get("unit") or "")
    expected = f["example"]["expected"]
    if isinstance(expected, bool) or not isinstance(expected, (int, float)):
        raise FormulaError(f"The worked example for {f['key']} must expect a number")
    if abs(got - expected) > 1e-9 * max(1.0, abs(expected)):
        raise FormulaError(f"The worked example for {f['key']} gives {got:g} but expects {expected:g}; "
                           "fix the formula or the example")
    return got


def validate_formula(raw, units: Optional[Dict[str, str]] = None, known: Optional[Iterable[str]] = None) -> dict:
    """A clean formula dict, with its worked example checked. ``known``: names the formula may use."""
    if not isinstance(raw, dict):
        raise FormulaError("A formula must be an object with key, label, expr, unit and example")
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise FormulaError("A formula key must be lower case letters, digits and _ (for example install_hours)")
    label = raw.get("label") or key
    if not isinstance(label, str):
        raise FormulaError(f"{key}: label must be text")
    expr = raw.get("expr")
    tree = parse(expr)
    unit = raw.get("unit") or ""
    parse_unit(unit)
    role = raw.get("role") or "line"
    if role not in ROLES:
        raise FormulaError(f"{key}: role must be one of {', '.join(ROLES)}")
    if known is not None:
        known = set(known)
        unknown = [n for n in names_in(tree) if n not in known]
        if unknown:
            raise FormulaError(f"{key} uses {', '.join(unknown)}, which is not a setting, a measurement or "
                               f"another formula. Known: {', '.join(sorted(known))}. Add a new setting "
                               "with request_inputs first")
    clean = {"key": key, "label": label.strip()[:100], "expr": expr.strip(), "unit": unit.strip(), "role": role,
             "description": str(raw.get("description") or "")[:500], "example": raw.get("example")}
    check_example(clean, units)
    clean["version"] = formula_version(clean)
    for extra in ("set_by", "set_at"):
        if raw.get(extra):
            clean[extra] = str(raw[extra])[:80]
    return clean


def _order(formulas: List[dict]) -> List[dict]:
    by_key = {f["key"]: f for f in formulas}
    deps = {f["key"]: [n for n in names_in(parse(f["expr"])) if n in by_key] for f in formulas}
    out, state = [], {}

    def visit(k, path):
        if state.get(k) == 2:
            return
        if state.get(k) == 1:
            raise FormulaError("Circular formulas: " + " -> ".join(path + [k]))
        state[k] = 1
        for d in deps[k]:
            visit(d, path + [k])
        state[k] = 2
        out.append(by_key[k])

    for f in formulas:
        visit(f["key"], [])
    return out


def evaluate_all(formulas: List[dict], fields: Dict[str, Any]) -> Dict[str, dict]:
    """Every formula's result {key: {value, unit, version, label, role}}, in dependency order."""
    env = {}
    for name, field in fields.items():
        try:
            env[name] = _quantity(name, field)
        except FormulaError:
            continue  # a field with no value only matters if a formula uses it
    results: Dict[str, dict] = {}
    for f in _order(formulas):
        tree = parse(f["expr"])
        absent = [n for n in names_in(tree) if n not in env and n in fields]
        if absent:
            raise FormulaError(f"{f['key']} needs {', '.join(absent)}, which has no value yet")
        value = _result(_eval(tree, env), f.get("unit") or "", f["key"])
        dims, scale = parse_unit(f.get("unit") or "")
        env[f["key"]] = Q(value * scale, dims)
        results[f["key"]] = {"key": f["key"], "label": f.get("label", f["key"]), "value": value,
                             "unit": f.get("unit") or "", "version": formula_version(f),
                             "role": f.get("role", "line")}
    return results


def needed_fields(formulas: List[dict]) -> List[str]:
    keys = {f["key"] for f in formulas}
    out: List[str] = []
    for f in formulas:
        for n in names_in(parse(f["expr"])):
            if n not in keys and n not in out:
                out.append(n)
    return out


def build_quote(formulas: List[dict], fields: Dict[str, Any], currency: str = "AUD") -> dict:
    """The quote: every formula's line, the total, and what it was made from (to reproduce it)."""
    results = evaluate_all(formulas, fields)
    lines = [results[f["key"]] for f in formulas]
    total = results.get("total", {}).get("value")
    return {"schema": "cardboardcad.quote/1", "currency": currency, "lines": lines, "total": total,
            "fields": copy.deepcopy(fields), "formulas": copy.deepcopy(formulas)}


def reproduce(quote: dict) -> dict:
    """Work out an old quote again from the formulas and fields it recorded."""
    return build_quote(quote["formulas"], quote["fields"], quote.get("currency", "AUD"))


def _ex(inputs: Dict[str, Tuple[float, str]], expected: float) -> dict:
    return {"inputs": {k: {"value": v, "unit": u} for k, (v, u) in inputs.items()}, "expected": expected}


BUILTIN_FORMULAS: List[dict] = [
    {"key": "cut_minutes", "label": "Machine time", "role": "working", "unit": "min",
     "expr": "(cut_length / cut_speed + score_length / score_speed + pierces * pierce_time) * (1 + travel_overhead)",
     "description": "Cutting, scoring and pierces, plus travel moves",
     "example": _ex({"cut_length": (1200, "mm"), "cut_speed": (20, "mm/s"), "score_length": (400, "mm"),
                     "score_speed": (40, "mm/s"), "pierces": (10, "count"), "pierce_time": (0.5, "s"),
                     "travel_overhead": (0.15, "fraction")}, 1.4375)},
    {"key": "material", "label": "Board", "role": "line", "unit": "AUD",
     "expr": "round(sheets * sheet_price, 2)", "description": "Sheets of board",
     "example": _ex({"sheets": (3, "sheet"), "sheet_price": (38.5, "AUD/sheet")}, 115.5)},
    {"key": "machine", "label": "Machine time", "role": "line", "unit": "AUD",
     "expr": "round(cut_minutes * machine_rate, 2)", "description": "Machine time at the machine rate",
     "example": _ex({"cut_minutes": (30, "min"), "machine_rate": (60, "AUD/h")}, 30)},
    {"key": "subtotal", "label": "Cost", "role": "subtotal", "unit": "AUD",
     "expr": "material + machine", "description": "Board and machine",
     "example": _ex({"material": (115.5, "AUD"), "machine": (30, "AUD")}, 145.5)},
    {"key": "margin", "label": "Margin", "role": "line", "unit": "AUD",
     "expr": "round(subtotal * margin_pct, 2)", "description": "Margin on cost",
     "example": _ex({"subtotal": (145.5, "AUD"), "margin_pct": (20, "%")}, 29.1)},
    {"key": "gst", "label": "GST", "role": "line", "unit": "AUD",
     "expr": "round((subtotal + margin) * gst_pct, 2)", "description": "Tax on cost and margin",
     "example": _ex({"subtotal": (145.5, "AUD"), "margin": (29.1, "AUD"), "gst_pct": (10, "%")}, 17.46)},
    {"key": "total", "label": "Total", "role": "total", "unit": "AUD",
     "expr": "subtotal + margin + gst", "description": "Quote total including GST",
     "example": _ex({"subtotal": (145.5, "AUD"), "margin": (29.1, "AUD"), "gst": (17.46, "AUD")}, 192.06)},
]
for _f in BUILTIN_FORMULAS:
    _f["version"] = formula_version(_f)


def _read_json(path: str):
    try:
        if os.path.getsize(path) > 1024 * 1024:
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


class FormulaStore:
    """The formulas in use: built in, then Fold Theory's saved ones, then the project's (by key).

    ``fields()`` gives {name: unit} for every setting and measurement a formula may use.
    """

    def __init__(self, home: str, project_folder: Callable[[], Optional[str]],
                 field_units: Callable[[], Dict[str, str]]):
        from .settings import settings_dir

        self.home = home
        self.defaults_path = os.path.join(settings_dir(home), FORMULAS_FILE)
        self._project_folder = project_folder
        self._field_units = field_units
        self.project_formulas: List[dict] = []  # kept in memory until the project is saved
        self.rejected: List[str] = []  # why saved formulas were not used (for diagnostics)

    def _file_formulas(self, data) -> List[dict]:
        out = []
        if isinstance(data, dict) and isinstance(data.get("formulas"), list):
            for raw in data["formulas"][:MAX_FORMULAS]:
                try:
                    out.append(validate_formula(raw, self._units_for(raw)))
                except FormulaError as exc:
                    self.rejected.append(f"{raw.get('key') if isinstance(raw, dict) else raw}: {exc}")
        return out

    def _units_for(self, raw) -> Dict[str, str]:
        units = dict(self._field_units())
        for f in BUILTIN_FORMULAS:
            units.setdefault(f["key"], f["unit"])
        return units

    def load_project(self, folder: Optional[str]) -> None:
        self.project_formulas = self._file_formulas(_read_json(os.path.join(folder, FORMULAS_FILE))) \
            if folder else []

    def clear_project(self) -> None:
        self.project_formulas = []

    def _defaults(self) -> List[dict]:
        return self._file_formulas(_read_json(self.defaults_path))

    def all(self) -> List[dict]:
        merged: Dict[str, dict] = {f["key"]: dict(f, source="builtin") for f in BUILTIN_FORMULAS}
        for f in self._defaults():
            merged[f["key"]] = dict(f, source="default")
        for f in self.project_formulas:
            merged[f["key"]] = dict(f, source="project")
        return list(merged.values())

    def project_file_text(self) -> str:
        return json.dumps({"schema": FORMULAS_SCHEMA, "formulas": self.project_formulas}, indent=2,
                          ensure_ascii=False) + "\n"

    def save_formula(self, raw, *, scope: str = "project", set_by: str = "") -> dict:
        from .project import _write_atomic
        from .settings import now_iso

        units = self._units_for(raw)
        existing = {f["key"] for f in self.all()}
        known = set(units) | existing
        if isinstance(raw, dict) and raw.get("key") in self._field_units():
            raise FormulaError(f"{raw['key']} is already a setting or measurement; give the formula another key")
        f = validate_formula({**raw, "set_by": set_by or "designer", "set_at": now_iso()}, units,
                             known - {raw.get("key") if isinstance(raw, dict) else None})
        trial = [x for x in self.all() if x["key"] != f["key"]] + [f]
        _order(trial)  # no cycles
        if scope == "all":
            data = _read_json(self.defaults_path)
            current = [x for x in (data.get("formulas", []) if isinstance(data, dict) else [])
                       if isinstance(x, dict) and x.get("key") != f["key"]]
            _write_atomic(self.defaults_path, json.dumps({"schema": FORMULAS_SCHEMA, "formulas": current + [f]},
                                                         indent=2, ensure_ascii=False) + "\n")
        elif scope == "project":
            self.project_formulas = [x for x in self.project_formulas if x["key"] != f["key"]] + [f]
            folder = self._project_folder()
            if folder:
                _write_atomic(os.path.join(folder, FORMULAS_FILE), self.project_file_text())
        else:
            raise FormulaError("scope must be project or all")
        return f
