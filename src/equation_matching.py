"""
Match CellML equations to equations in the paper by comparing their expression trees.

CellML:  i_b_Na = g_bna * (V - E_Na)
Paper:   I_{b, Na} = G_{bna}(V - E_{Na})

Both are parsed into canonical trees (src/expr_tree.py) and aligned node by node.
Sums and products are unordered, so their children are paired greedily by best score.
Where two variables meet in the alignment we learn a mapping (i_b_Na -> I_b,Na,
g_bna -> G_bna, ...). Variables score higher when their names look alike, which breaks
ties between equations with the same shape (e.g. I_b,Na and I_b,Ca). A variable pair
scores its name similarity (0..1); every matching operator node scores 1.

The similarity of two equations is 2 * score / (size_cellml + size_paper), from 0 to 1.
In a large equation the defined variable is a single node, so candidates are ranked by
    confidence = similarity * (0.5 + 0.5 * name similarity of the two left-hand sides)
which keeps I_{Ca,L,Na,ds} from winning for i_Ca_L_Ca_ds just because its shape fits a bit better.

Papers often write in one equation what CellML splits in two (jafri: I_Ca = P_Ca y (O + O_Ca) ...
vs i_Ca_L_Ca = i_Ca_L_Ca_max * y * (O + O_Ca) and a separate i_Ca_L_Ca_max = ...). So each CellML
equation is also tried with one of its right-hand-side variables replaced by its own definition.
"""

import re
from collections import defaultdict
from difflib import SequenceMatcher

from src import expr_tree as T
from src.latex_to_tree import parse_latex

# A CellML equation is linked to its best paper equation only above both thresholds
MIN_SIMILARITY = 0.6
MIN_CONFIDENCE = 0.5
# Score of a pair of numbers that differ (unit scaling often changes constants)
NUMBER_MISMATCH = 0.25
# Weight of the left-hand-side mapping of a linked equation compared with mappings inside it
LHS_WEIGHT = 3.0
# A paper symbol is reported for a CellML variable only with this much evidence
MIN_SYMBOL_SCORE = 0.5


def _name_key(name):
    """'[Ca2+]_i' -> 'cai', 'I_{b,Na}' -> 'ibna' (for name similarity only)."""
    name = re.sub(r"\d*[+-](?=\])", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", name)


def name_similarity(a, b):
    return SequenceMatcher(None, _name_key(a), _name_key(b)).ratio()


class _Aligner:
    """Aligns one CellML tree with one paper tree; caches sub-results by node identity."""

    def __init__(self):
        self.memo = {}

    def align(self, c, p):
        """Return (score, pairs) where pairs are (cellml_variable, paper_symbol, leaf_score)."""
        key = (id(c), id(p))
        if key not in self.memo:
            self.memo[key] = self._align(c, p)
        return self.memo[key]

    def _align(self, c, p):
        kc, kp = c[0], p[0]
        if kc == "v" and kp == "v":
            score = name_similarity(c[1], p[1])
            return score, ((c[1], p[1], score),)
        if kc == "n" and kp == "n":
            return (1.0 if T.is_close(c[1], p[1]) else NUMBER_MISMATCH), ()
        if kc == "d" and kp == "v":
            # The paper lost the dot of a derivative: "[Ca^{2+}]_i = ..."
            return self.align(c[1], p)
        if kc != kp:
            return 0.0, ()
        if kc in ("+", "*"):
            score, pairs = self._align_unordered(c[1], p[1])
            return 1 + score, pairs
        if kc == "f" and c[1] != p[1]:
            return 0.0, ()
        return self._align_ordered(T.children(c), T.children(p))

    def _align_ordered(self, cs, ps):
        if len(cs) != len(ps):
            return 0.0, ()
        score, pairs = 1.0, ()
        for c, p in zip(cs, ps):
            s, pp = self.align(c, p)
            score += s
            pairs += pp
        return score, pairs

    def _align_unordered(self, cs, ps):
        candidates = []
        for i, c in enumerate(cs):
            for j, p in enumerate(ps):
                s, pp = self.align(c, p)
                if s > 0:
                    candidates.append((s, i, j, pp))
        candidates.sort(key=lambda x: -x[0])
        used_c, used_p = set(), set()
        score, pairs = 0.0, ()
        for s, i, j, pp in candidates:
            if i not in used_c and j not in used_p:
                used_c.add(i)
                used_p.add(j)
                score += s
                pairs += pp
        return score, pairs


def similarity(cellml_tree, paper_tree):
    """(similarity 0..1, variable pairs) of two equation trees."""
    score, pairs = _Aligner().align(cellml_tree, paper_tree)
    return 2 * score / (T.size(cellml_tree) + T.size(paper_tree)), pairs


def _lhs_variable(tree):
    lhs = tree[1]
    if lhs[0] == "d":
        lhs = lhs[1]
    return lhs[1] if lhs[0] == "v" else None


def _variants(tree, definitions):
    """The equation itself, plus one variant per right-hand-side variable replaced by its definition."""
    variants = [tree]
    lhs, rhs = tree[1], tree[2]
    for name in sorted(T.variables(rhs)):
        if name in definitions and definitions[name] != rhs:
            variants.append(T.equation(lhs, T.substitute(rhs, name, definitions[name])))
    return variants


def parse_paper_equations(formulas):
    """Parse the paper's formulas: [(formula, tree)] for every equation that could be parsed."""
    parsed = []
    for formula in formulas:
        for tree in parse_latex(formula["latex"]):
            parsed.append((formula, tree))
    return parsed


def match_equations(cellml_equations, formulas, min_similarity=MIN_SIMILARITY, min_confidence=MIN_CONFIDENCE):
    """Link CellML equations to paper equations and derive variable -> paper symbol mappings.

    Args:
        cellml_equations: [{"component", "text", "tree"}] from cellml_reader.read_equations
        formulas: [{"id", "page", "type", "latex"}] from stage1_paper.json

    Returns:
        {
          "parsed_paper_equations": int,
          "equations": [{"variable", "component", "cellml_formula", "formula_id", "page",
                         "latex", "similarity", "confidence"}],   one per linked CellML equation
          "symbols": {cellml_variable: [{"symbol", "score", "formula_ids"}]}
        }
    """
    paper = parse_paper_equations(formulas)
    paper_sizes = [T.size(tree) for _, tree in paper]

    # Algebraic definitions (x = expression with variables), used to inline intermediate variables
    definitions = {}
    for equation in cellml_equations:
        tree = equation["tree"]
        if tree[0] == "=" and tree[1][0] == "v" and T.variables(tree[2]):
            definitions[tree[1][1]] = tree[2]

    links = []
    votes = defaultdict(lambda: defaultdict(lambda: {"score": 0.0, "formula_ids": set()}))

    for equation in cellml_equations:
        tree = equation["tree"]
        variable = _lhs_variable(tree) if tree[0] == "=" else None
        if variable is None:
            continue

        best = None
        for variant in _variants(tree, definitions):
            size = T.size(variant)
            for (formula, paper_tree), paper_size in zip(paper, paper_sizes):
                # Equations of very different size cannot reach the threshold
                if 2 * min(size, paper_size) / (size + paper_size) < min_similarity:
                    continue
                sim, pairs = similarity(variant, paper_tree)
                paper_variable = _lhs_variable(paper_tree)
                lhs_similarity = name_similarity(variable, paper_variable) if paper_variable else 0.0
                confidence = sim * (0.5 + 0.5 * lhs_similarity)
                if best is None or confidence > best[0]:
                    best = (confidence, sim, formula, pairs)

        if best is None or best[1] < min_similarity or best[0] < min_confidence:
            continue
        confidence, sim, formula, pairs = best
        links.append({
            "variable": variable,
            "component": equation["component"],
            "cellml_formula": equation["text"],
            "formula_id": formula["id"],
            "page": formula["page"],
            "latex": formula["latex"],
            "similarity": round(sim, 3),
            "confidence": round(confidence, 3),
        })
        for cellml_var, symbol, leaf_score in pairs:
            vote = votes[cellml_var][symbol]
            # The defined variable (left-hand side) is the most reliable mapping
            weight = LHS_WEIGHT if cellml_var == variable else 1.0
            vote["score"] += weight * confidence * leaf_score
            vote["formula_ids"].add(formula["id"])

    symbols = {}
    for cellml_var, candidates in votes.items():
        ranked = sorted(candidates.items(), key=lambda kv: -kv[1]["score"])
        top = ranked[0][1]["score"]
        if top < MIN_SYMBOL_SCORE:
            continue
        symbols[cellml_var] = [
            {"symbol": symbol, "score": round(v["score"], 3), "formula_ids": sorted(v["formula_ids"])}
            for symbol, v in ranked if v["score"] >= max(MIN_SYMBOL_SCORE, 0.5 * top)
        ]

    return {"parsed_paper_equations": len(paper), "equations": links, "symbols": symbols}
