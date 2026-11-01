"""
Convert CellML (content MathML) equations into readable infix text,
e.g. <apply><eq/><ci>i_L</ci>...</apply>  ->  "i_L = g_L * (V - E_L)".
"""

import math
import re
import xml.etree.ElementTree as ET

from src import expr_tree as T

MATHML_NS = "http://www.w3.org/1998/Math/MathML"

# Operator precedence (higher binds tighter)
PREC_ATOM = 100
PREC_POWER = 40
PREC_UNARY = 35
PREC_MUL = 30
PREC_ADD = 20
PREC_REL = 10
PREC_AND = 6
PREC_OR = 5
PREC_EQ = 0

BINARY_OPS = {
    "plus": (" + ", PREC_ADD),
    "minus": (" - ", PREC_ADD),
    "times": (" * ", PREC_MUL),
    "divide": (" / ", PREC_MUL),
    "eq": (" = ", PREC_EQ),
    "neq": (" != ", PREC_REL),
    "lt": (" < ", PREC_REL),
    "gt": (" > ", PREC_REL),
    "leq": (" <= ", PREC_REL),
    "geq": (" >= ", PREC_REL),
    "and": (" and ", PREC_AND),
    "or": (" or ", PREC_OR),
    "xor": (" xor ", PREC_OR),
}

FUNCTIONS = {
    "exp", "ln", "abs", "floor", "ceiling", "factorial",
    "sin", "cos", "tan", "sec", "csc", "cot",
    "sinh", "cosh", "tanh", "sech", "csch", "coth",
    "arcsin", "arccos", "arctan", "arcsec", "arccsc", "arccot",
    "arcsinh", "arccosh", "arctanh", "arcsech", "arccsch", "arccoth",
    "min", "max", "rem",
}

CONSTANTS = {
    "pi": "pi",
    "exponentiale": "e",
    "true": "true",
    "false": "false",
    "notanumber": "NaN",
    "infinity": "inf",
}


def _tag(el):
    return el.tag.split("}", 1)[-1]


def _wrap(text, prec, min_prec):
    return f"({text})" if prec < min_prec else text


def _qualifier(el, name):
    """Return the first child of a qualifier element such as <degree> or <logbase>."""
    for child in el:
        if _tag(child) == name and len(child):
            return child[0]
    return None


def _cn(el):
    if el.get("type") == "e-notation":
        parts = [t.strip() for t in el.itertext()]
        return f"{parts[0]}e{parts[1]}" if len(parts) == 2 else "".join(parts)
    return (el.text or "").strip()


def _apply(el):
    children = list(el)
    op = _tag(children[0])
    args = [c for c in children[1:] if _tag(c) not in ("bvar", "degree", "logbase")]

    if op in BINARY_OPS:
        symbol, prec = BINARY_OPS[op]
        if op == "minus" and len(args) == 1:
            text, p = _expr(args[0])
            return "-" + _wrap(text, p, PREC_UNARY + 1), PREC_UNARY
        if op == "plus" and len(args) == 1:
            return _expr(args[0])
        parts = []
        for i, arg in enumerate(args):
            text, p = _expr(arg)
            # Left-associative: right operands of - and / need tighter binding
            needs = prec + 1 if (i > 0 and op in ("minus", "divide")) else prec
            if op == "times" and p == PREC_UNARY:
                needs = PREC_ATOM
            parts.append(_wrap(text, p, needs))
        return symbol.join(parts), prec

    if op == "not":
        text, p = _expr(args[0])
        return "not " + _wrap(text, p, PREC_UNARY), PREC_UNARY

    if op == "power":
        base, pb = _expr(args[0])
        exponent, pe = _expr(args[1])
        return f"{_wrap(base, pb, PREC_POWER + 1)}^{_wrap(exponent, pe, PREC_ATOM)}", PREC_POWER

    if op == "root":
        degree = _qualifier(el, "degree")
        radicand = _expr(args[0])[0]
        if degree is None:
            return f"sqrt({radicand})", PREC_ATOM
        return f"root({radicand}, {_expr(degree)[0]})", PREC_ATOM

    if op == "log":
        base = _qualifier(el, "logbase")
        arg = _expr(args[0])[0]
        if base is None:
            return f"log10({arg})", PREC_ATOM
        return f"log({arg}, {_expr(base)[0]})", PREC_ATOM

    if op == "diff":
        bvar = _qualifier(el, "bvar")
        wrt = _expr(bvar)[0] if bvar is not None else "t"
        return f"d({_expr(args[0])[0]})/d{wrt}", PREC_ATOM

    if op in FUNCTIONS:
        return f"{op}({', '.join(_expr(a)[0] for a in args)})", PREC_ATOM

    # Unknown operator: render generically so nothing is silently dropped
    return f"{op}({', '.join(_expr(a)[0] for a in args)})", PREC_ATOM


def _piecewise(el):
    cases = []
    otherwise = None
    for child in el:
        if _tag(child) == "piece":
            value, cond = _expr(child[0])[0], _expr(child[1])[0]
            cases.append(f"{value} if {cond}")
        elif _tag(child) == "otherwise":
            otherwise = _expr(child[0])[0]
    if otherwise is not None:
        cases.append(f"{otherwise} otherwise")
    return "{ " + "; ".join(cases) + " }", PREC_ATOM


def _expr(el):
    """Return (text, precedence) for a MathML element."""
    tag = _tag(el)
    if tag == "ci":
        return (el.text or "").strip(), PREC_ATOM
    if tag == "cn":
        return _cn(el), PREC_ATOM
    if tag in CONSTANTS:
        return CONSTANTS[tag], PREC_ATOM
    if tag == "apply":
        return _apply(el)
    if tag == "piecewise":
        return _piecewise(el)
    if tag in ("bvar", "degree", "logbase", "semantics") and len(el):
        return _expr(el[0])
    return tag, PREC_ATOM


def parse_equations(math_string):
    """Parse the MathML of one component into a list of top-level <eq> elements."""
    if not math_string or not math_string.strip():
        return []
    # A component may hold several <math> blocks; wrap them so they parse as one document.
    # Some models use attribute prefixes (e.g. cmeta:id) without declaring them, so bind
    # every prefix on the wrapper; declarations inside the <math> blocks still take precedence.
    prefixes = set(re.findall(r"\s([A-Za-z_][\w.-]*):[\w.-]+\s*=", math_string)) - {"xmlns", "xml"}
    declarations = "".join(f' xmlns:{p}="urn:prefix:{p}"' for p in sorted(prefixes))
    root = ET.fromstring(f"<root{declarations}>{math_string}</root>")
    equations = []
    for math in root.iter(f"{{{MATHML_NS}}}math"):
        for apply in math:
            if _tag(apply) == "apply" and len(apply) and _tag(apply[0]) == "eq":
                equations.append(apply)
    return equations


def equation_defines(equation, variable_name):
    """True if the left-hand side of the equation is `variable_name` or d(variable_name)/dt."""
    lhs = equation[1]
    if _tag(lhs) == "ci":
        return (lhs.text or "").strip() == variable_name
    if _tag(lhs) == "apply" and _tag(lhs[0]) == "diff":
        targets = [c for c in lhs[1:] if _tag(c) == "ci"]
        return bool(targets) and (targets[0].text or "").strip() == variable_name
    return False


def equation_to_text(equation):
    return _expr(equation)[0]


# --- Canonical tree (used by equation matching) -------------------------------------

def _tree_cn(el):
    try:
        return T.num(_cn(el))
    except ValueError:
        return T.var(_cn(el))


def _tree_apply(el):
    children = list(el)
    op = _tag(children[0])
    args = [_tree(c) for c in children[1:] if _tag(c) not in ("bvar", "degree", "logbase")]

    if op == "eq" and len(args) == 2:
        return T.equation(args[0], args[1])
    if op == "plus":
        return T.add(*args)
    if op == "minus":
        return T.neg(args[0]) if len(args) == 1 else T.sub(args[0], args[1])
    if op == "times":
        return T.mul(*args)
    if op == "divide":
        return T.div(args[0], args[1])
    if op == "power":
        return T.power(args[0], args[1])
    if op == "root":
        degree = _qualifier(el, "degree")
        exponent = T.div(T.ONE, _tree(degree)) if degree is not None else T.num(0.5)
        return T.power(args[0], exponent)
    if op == "diff":
        return T.diff(args[0])
    return T.func(op, *args)


def _tree(el):
    tag = _tag(el)
    if tag == "ci":
        return T.var((el.text or "").strip())
    if tag == "cn":
        return _tree_cn(el)
    if tag == "pi":
        return T.num(math.pi)
    if tag == "exponentiale":
        return T.num(math.e)
    if tag in CONSTANTS:
        return T.var(CONSTANTS[tag])
    if tag == "apply":
        return _tree_apply(el)
    if tag == "piecewise":
        # Keep only the values; conditions rarely appear in the same form in a paper
        values = [_tree(child[0]) for child in el if _tag(child) in ("piece", "otherwise") and len(child)]
        return values[0] if len(values) == 1 else T.func("piecewise", *values)
    if tag in ("bvar", "degree", "logbase", "semantics") and len(el):
        return _tree(el[0])
    return T.var(tag)


def equation_to_tree(equation):
    """Canonical expression tree (see src/expr_tree.py) of a MathML <eq> element."""
    return _tree(equation)
