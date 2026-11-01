"""
Canonical expression trees shared by CellML (MathML) and paper (LaTeX) equations.

Both sides are built with the constructors below, so equivalent formulas end up with
the same shape no matter how they were written:
    a - b      -> ("+", (a, ("*", (("n", -1.0), b))))
    a / b      -> ("*", (a, ("^", b, ("n", -1.0))))
    nested sums and products are flattened, numeric factors are folded into one number.

Nodes (plain tuples, so they are hashable):
    ("v", name)             variable / symbol
    ("n", value)            number
    ("+", (terms...))       sum
    ("*", (factors...))     product
    ("^", base, exponent)   power
    ("f", name, (args...))  function, e.g. exp, ln
    ("d", node)             time derivative
    ("=", lhs, rhs)         equation
"""

import math

ONE = ("n", 1.0)
MINUS_ONE = ("n", -1.0)


def var(name):
    return ("v", name)


def num(value):
    return ("n", float(value))


def add(*terms):
    flat = []
    for term in terms:
        if term[0] == "+":
            flat.extend(term[1])
        elif term != ("n", 0.0):
            flat.append(term)
    if not flat:
        return ("n", 0.0)
    return flat[0] if len(flat) == 1 else ("+", tuple(flat))


def mul(*factors):
    flat = []
    for factor in factors:
        flat.extend(factor[1] if factor[0] == "*" else [factor])
    product = 1.0
    others = []
    for factor in flat:
        if factor[0] == "n":
            product *= factor[1]
        else:
            others.append(factor)
    if product == 0:
        return ("n", 0.0)
    if product != 1 or not others:
        others.insert(0, ("n", product))
    return others[0] if len(others) == 1 else ("*", tuple(others))


def neg(x):
    return mul(MINUS_ONE, x)


def sub(a, b):
    return add(a, neg(b))


def power(base, exponent):
    if exponent == ONE:
        return base
    if base[0] == "n" and exponent[0] == "n":
        try:
            return num(base[1] ** exponent[1])
        except (OverflowError, ZeroDivisionError, ValueError):
            pass
    return ("^", base, exponent)


def div(a, b):
    return mul(a, power(b, MINUS_ONE))


def func(name, *args):
    return ("f", name, tuple(args))


def diff(x):
    return ("d", x)


def equation(lhs, rhs):
    return ("=", lhs, rhs)


def children(node):
    kind = node[0]
    if kind in ("+", "*"):
        return node[1]
    if kind == "f":
        return node[2]
    if kind in ("^", "="):
        return node[1:]
    if kind == "d":
        return (node[1],)
    return ()


def substitute(node, name, replacement):
    """Replace variable `name` by `replacement`, rebuilding so the result stays canonical."""
    kind = node[0]
    if kind == "v":
        return replacement if node[1] == name else node
    if kind == "n":
        return node
    new = [substitute(c, name, replacement) for c in children(node)]
    if kind == "+":
        return add(*new)
    if kind == "*":
        return mul(*new)
    if kind == "^":
        return power(*new)
    if kind == "f":
        return func(node[1], *new)
    if kind == "d":
        return diff(new[0])
    return equation(*new)


def size(node):
    return 1 + sum(size(c) for c in children(node))


def variables(node):
    """All variable names in a tree."""
    if node[0] == "v":
        return {node[1]}
    names = set()
    for child in children(node):
        names |= variables(child)
    return names


def is_close(a, b):
    return math.isclose(a, b, rel_tol=1e-3, abs_tol=1e-12)
