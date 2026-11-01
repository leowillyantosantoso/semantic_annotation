"""
Parse LaTeX equations from the paper (as returned by LlamaParse) into the canonical
expression trees of src/expr_tree.py, so they can be compared with CellML equations.

The parser is deliberately tolerant: PDF extraction produces things like "I_p, Na"
(broken subscripts), "C a^{2+}" (spaced letters) and implicit multiplication
("4P_{Ca}d(1 - f)"). An equation that still cannot be parsed returns None.
"""

import re

from src import expr_tree as T

FUNCTIONS = {
    "exp", "ln", "log", "sin", "cos", "tan", "sinh", "cosh", "tanh",
    "arcsin", "arccos", "arctan", "min", "max", "abs",
}
FRACTIONS = {"frac", "dfrac", "tfrac"}
DERIVATIVES = {"dot", "ddot"}
# Commands that end an expression (relations in conditions)
STOP_COMMANDS = {"le", "leq", "ge", "geq", "lt", "gt", "ne", "neq", "approx", "sim"}

# A name may end in digits only when a subscript follows (K1_x)
TOKEN_PATTERN = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)|\\([A-Za-z]+)|([A-Za-z]+(?:\d+(?=_))?)|(\S))")
TEXT_COMMAND = re.compile(r"\\(text|mathrm|mathit|mathbf|operatorname|rm|bar|hat|tilde|overline|widetilde|widehat)\s*\{([^{}]*)\}")
EQUATION_SEPARATOR = re.compile(r";|\s+and\s+")
SYMBOL_PATTERN = re.compile(r"(?<![\\A-Za-z])[A-Za-z]+(?:_(?:\{[^{}]*\}|[A-Za-z0-9]+))?")


class ParseError(Exception):
    pass


def clean_latex(latex):
    """Remove layout commands and repair common PDF-extraction damage."""
    s = latex
    s = re.sub(r"\\(begin|end)\{[A-Za-z*]+\}", " ", s)
    s = s.replace("\\\\", " ").replace("&", " ")
    s = re.sub(r"\\(left|right)\.", " ", s)
    s = re.sub(r"\\(left|right|big|Big|bigg|Bigg)(?![A-Za-z])", "", s)
    s = re.sub(r"\\q?quad(?![A-Za-z])", ";", s)  # "a = 1, \\quad b = 2": two equations
    s = re.sub(r"\\displaystyle(?![A-Za-z])|\\[,;:! ]", " ", s)
    # Font and accent commands keep only their content; repeat for nested ones (\overline{\text{G}})
    previous = None
    while previous != s:
        previous = s
        s = TEXT_COMMAND.sub(r"\2", s)
    # Conditions of piecewise definitions: "T = 0  for  SL \le 1"
    s = re.sub(r"\s(for|if|when|where)\s.*$", "", s)
    s = s.strip().rstrip(".,;:").strip()
    # "1 \cdot 2" is an OCR'd decimal point (1·2), but "3 \cdot 10^5" is a product
    s = re.sub(r"(?<![\d.])(\d)\s*\\cdot\s*(\d+)(?![\d.]|\s*\^)", r"\1.\2", s)
    s = re.sub(r"\\(cdot|times)(?![A-Za-z])", "*", s)
    s = s.replace("\\{", "(").replace("\\}", ")")
    s = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", s)  # thousands separator: 1,000
    # Broken subscripts: "I_p, Na" -> "I_{p,Na}", "ICa_L,Ca,ds" -> "ICa_{L,Ca,ds}"
    s = re.sub(r"([A-Za-z])_([A-Za-z0-9]+(?:,\s*[A-Za-z][A-Za-z0-9]*)+)",
               lambda m: m.group(1) + "_{" + re.sub(r"\s", "", m.group(2)) + "}", s)
    return s


def _tokenize(latex):
    tokens = []
    for match in TOKEN_PATTERN.finditer(latex):
        number, command, name, symbol = match.groups()
        if number:
            tokens.append(("num", number))
        elif command:
            tokens.append(("cmd", command))
        elif name:
            tokens.append(("name", name))
        elif symbol:
            tokens.append(("sym", symbol))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset=0):
        i = self.pos + offset
        return self.tokens[i] if i < len(self.tokens) else ("end", "")

    def next(self):
        token = self.peek()
        self.pos += 1
        return token

    def accept(self, symbol):
        if self.peek() == ("sym", symbol):
            self.pos += 1
            return True
        return False

    def expect(self, symbol):
        if not self.accept(symbol):
            raise ParseError(f"expected '{symbol}' at token {self.pos}, got {self.peek()}")

    def at_end(self):
        return self.pos >= len(self.tokens)

    # --- grammar ---------------------------------------------------------------

    def equation(self):
        lhs = self.expr()
        self.expect("=")
        rhs = self.expr()
        if not self.at_end():
            raise ParseError(f"unexpected {self.peek()} after equation")
        return T.equation(lhs, rhs)

    def expr(self):
        negative = self.accept("-")
        if not negative:
            self.accept("+")
        first = self.term()
        terms = [T.neg(first) if negative else first]
        while self.peek() in (("sym", "+"), ("sym", "-")):
            op = self.next()[1]
            term = self.term()
            terms.append(term if op == "+" else T.neg(term))
        return T.add(*terms)

    def starts_factor(self):
        kind, text = self.peek()
        if kind in ("num", "name"):
            return True
        if kind == "cmd":
            return text not in STOP_COMMANDS
        return kind == "sym" and text in "([{"

    def term(self):
        result = self.factor()
        while True:
            if self.accept("*"):
                result = T.mul(result, self.factor())
            elif self.accept("/"):
                # "RT/2F" is written for RT/(2F): a juxtaposed product stays in the denominator
                denominator = self.factor()
                while self.starts_factor():
                    denominator = T.mul(denominator, self.factor())
                result = T.div(result, denominator)
            elif self.starts_factor():
                result = T.mul(result, self.factor())
            else:
                return result

    def factor(self):
        if self.accept("-"):
            return T.neg(self.factor())
        base = self.atom()
        while True:
            if self.accept("^"):
                base = T.power(base, self.argument())
            elif not self.accept("'"):
                return base

    def argument(self):
        """Argument of ^ or a command: a {group} or a single atom."""
        if self.accept("{"):
            inner = self.expr()
            self.expect("}")
            return inner
        if self.accept("-"):
            return T.neg(self.atom())
        return self.atom()

    def subscript(self):
        """Raw text of a subscript, without spaces: _{Ca, L} -> 'Ca,L'."""
        if not self.accept("{"):
            return self.next()[1]
        depth, parts = 1, []
        while True:
            kind, text = self.next()
            if kind == "end":
                raise ParseError("unclosed subscript")
            if (kind, text) == ("sym", "{"):
                depth += 1
            elif (kind, text) == ("sym", "}"):
                depth -= 1
                if depth == 0:
                    return "".join(parts)
            parts.append(text)

    def named(self, name):
        # Juxtaposed physical constants written as one word: "RT", "RTF"
        if len(name) > 1 and set(name) <= set("RTF"):
            return T.mul(*(T.var(c) for c in name))
        if self.accept("'"):
            name += "'"
        if self.accept("_"):
            name = f"{name}_{self.subscript()}"
        return T.var(name)

    def atom(self):
        kind, text = self.next()
        if kind == "num":
            return T.num(text)
        if kind == "name":
            if text == "e" and self.peek() == ("sym", "^"):
                self.next()
                return T.func("exp", self.argument())
            return self.named(text)
        if kind == "cmd":
            return self.command(text)
        if kind == "sym":
            if text == "(":
                inner = self.expr()
                self.expect(")")
                return inner
            if text == "{":
                inner = self.expr()
                self.expect("}")
                return inner
            if text == "[":
                return self.bracket()
        raise ParseError(f"unexpected {kind} '{text}'")

    def command(self, name):
        if name in FRACTIONS:
            numerator = self.argument()
            denominator = self.argument()
            # \frac{dV}{dt} is a derivative
            if numerator[0] == "v" and numerator[1].startswith("d") and len(numerator[1]) > 1 and denominator == T.var("dt"):
                return T.diff(T.var(numerator[1][1:]))
            return T.div(numerator, denominator)
        if name == "sqrt":
            if self.accept("["):
                degree = self.expr()
                self.expect("]")
                return T.power(self.argument(), T.div(T.ONE, degree))
            return T.power(self.argument(), T.num(0.5))
        if name in DERIVATIVES:
            inner = self.argument()
            if inner[0] == "v" and self.accept("_"):
                inner = T.var(f"{inner[1]}_{self.subscript()}")
            return T.diff(inner)
        if name in FUNCTIONS:
            return T.func(name, self.factor())
        if name in STOP_COMMANDS:
            raise ParseError(f"relation \\{name}")
        # Greek letters and anything else: a symbol
        return self.named(name)

    def bracket(self):
        """[Na^+]_i is a concentration symbol; any other [...] is grouping."""
        start = self.pos
        concentration = self.concentration()
        if concentration is not None:
            return concentration
        self.pos = start
        inner = self.expr()
        self.expect("]")
        return inner

    def concentration(self):
        derivative = False
        if self.peek() in (("cmd", "dot"), ("cmd", "ddot")):
            self.next()
            if not self.accept("{"):
                return None
            derivative = True
        letters = []
        while self.peek()[0] == "name":
            letters.append(self.next()[1])
        if not letters or (derivative and not self.accept("}")):
            return None
        charge = ""
        if self.accept("^"):
            braced = self.accept("{")
            if self.peek()[0] == "num":
                charge += self.next()[1]
            if self.peek() not in (("sym", "+"), ("sym", "-")):
                return None
            charge += self.next()[1]
            if braced and not self.accept("}"):
                return None
        if not self.accept("]"):
            return None
        name = f"[{''.join(letters)}{charge}]"
        if self.accept("_"):
            name = f"{name}_{self.subscript()}"
        node = T.var(name)
        return T.diff(node) if derivative else node


def _parse(latex):
    try:
        return _Parser(_tokenize(latex)).equation()
    except (ParseError, IndexError, ZeroDivisionError, OverflowError):
        return None


def parse_latex(latex):
    """Parse a LaTeX formula into a list of canonical equation trees.

    One display block may hold several equations ("a = 1; b = 2", "a = b = 0"); each
    becomes its own tree. Parts that cannot be parsed are skipped.
    """
    equations = []
    for part in EQUATION_SEPARATOR.split(clean_latex(latex)):
        sides = part.strip().strip(".,;:").split("=")
        if len(sides) < 2 or not all(side.strip() for side in sides):
            continue
        # "alpha_h = alpha_j = 0" -> alpha_h = 0 and alpha_j = 0
        for lhs in sides[:-1]:
            tree = _parse(f"{lhs} = {sides[-1]}")
            if tree is not None:
                equations.append(tree)
    return equations


def latex_symbols(latex, trees=()):
    """Symbols that appear in a LaTeX formula, e.g. {'I_{K1}', 'G_{K1}', '[K+]_o', ...}."""
    symbols = set(SYMBOL_PATTERN.findall(clean_latex(latex)))
    for tree in trees:
        symbols |= T.variables(tree)
    return symbols
