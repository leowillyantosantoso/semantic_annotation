import re
from functools import lru_cache

from src.latex_to_tree import latex_symbols, parse_latex

# Name parts that only say where a current flows or which ion carries it. CellML models split
# currents this way (i_Ca_L_Na_ds) while papers often only name the whole current (I_{Ca,L}).
COMPARTMENT_SUFFIXES = {"cyt", "ds", "diadic", "dyadic", "bulk", "ss", "sl", "jct", "myo"}
ION_SUFFIXES = {"Na", "K", "Ca", "Cl"}


def _normalize_symbol(text):
    """Make a symbol comparable across notations: I_{Na}, I_Na, $I_{Na}$ and INa all become INa,
    I_{Ca, L} and I_CaL both become ICaL."""
    text = re.sub(r"\\(bar|overline|mathrm|mathit|text|hat|tilde)\b", "", text)
    return re.sub(r"[\\{}_$\s,]", "", text)


def _normalize_sentence(sentence):
    """Normalize LaTeX symbols inside a sentence word by word, keeping the spaces between words."""
    return " ".join(_normalize_symbol(word) for word in sentence.split())


@lru_cache(maxsize=None)
def _formula_symbols(latex):
    """Normalized (all symbols, left-hand-side symbols) of a formula, each joined by spaces
    so the sentence patterns apply."""
    trees = parse_latex(latex)
    symbols = {_normalize_symbol(s) for s in latex_symbols(latex, trees)}
    defined = set()
    for tree in trees:
        lhs = tree[1][1] if tree[1][0] == "d" else tree[1]
        if lhs[0] == "v":
            defined.add(_normalize_symbol(lhs[1]))
    return " ".join(sorted(symbols)), " ".join(sorted(defined))


def _symbol_pattern(symbol):
    # Currents are written i_Na in some papers and I_Na in others
    first = "[iI]" if symbol[0] in "iI" else re.escape(symbol[0])
    return re.compile(r"(?<![A-Za-z0-9])" + first + re.escape(symbol[1:]) + r"(?![A-Za-z0-9])")


def _keywords(synonyms):
    keywords = []

    # Textual synonyms (e.g. "leakage current"): case-insensitive phrase match
    for keyword in synonyms.get("textual", []):
        keyword = keyword.strip()

        # Skip keyword yang terlalu pendek karena mudah menghasilkan false positive
        if len(keyword) < 3:
            continue

        pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
        keywords.append(("textual", keyword, re.compile(pattern, re.IGNORECASE)))

    # Symbolic synonyms (e.g. "I_Na", "iL"): case-sensitive match on normalized symbols,
    # so the paper's LaTeX "I_{Na}" matches the synonym "INa"
    for keyword in synonyms.get("symbolic", []):
        symbol = _normalize_symbol(keyword)

        # Symbols of a single character (e.g. "I") match almost everywhere
        if len(symbol) < 2:
            continue

        keywords.append(("symbolic", keyword, _symbol_pattern(symbol)))

    return keywords


def formula_context(formula, sentences_by_id, keyword, match_type, source, **extra):
    """Context entry for a display formula, with the sentences printed around it."""
    nearby = []
    for key in ("sentence_before", "sentence_after"):
        sentence_id = formula.get(key)
        if sentence_id and sentence_id not in [s["sentence_id"] for s in nearby]:
            nearby.append({"sentence_id": sentence_id, "sentence": sentences_by_id[sentence_id]["text"]})
    label = formula.get("label")
    return {
        "sentence_id": None,
        "formula_id": formula["id"],
        "label": label,
        "page": formula["page"],
        "keyword": keyword,
        "match_type": match_type,
        "source": source,
        "location": "formula",
        "sentence": f"[Eq. {label}] {formula['latex']}" if label else formula["latex"],
        "nearby_sentences": nearby,
        **extra,
    }


def search_context(sentences, synonyms, formulas=(), source="llm"):
    """Find sentences and display formulas that mention a variable.

    Args:
        sentences: List of {"id", "page", "text"} from stage1_paper.json
        synonyms: Dict with "symbolic" and "textual" lists
        formulas: List of {"id", "page", "type", "latex", ...} from stage1_paper.json.
                  Only display formulas are searched (inline ones are part of the sentences),
                  and only with symbolic synonyms.
        source: Where the synonyms came from ("llm", "equation", "parent"), copied to each result

    Returns:
        List of {"sentence_id", "page", "keyword", "match_type", "source", "location", "sentence"};
        formula results also carry "formula_id", "label", "nearby_sentences" and "defines"
        (True when the symbol is the left-hand side, i.e. the formula defines the variable)
    """
    results = []
    keywords = _keywords(synonyms)

    # Cari keyword pada setiap sentence
    for sentence in sentences:
        normalized = _normalize_sentence(sentence["text"])

        for match_type, keyword, pattern in keywords:
            target = sentence["text"] if match_type == "textual" else normalized

            if pattern.search(target):

                results.append({
                    "sentence_id": sentence["id"],
                    "page": sentence["page"],
                    "keyword": keyword,
                    "match_type": match_type,
                    "source": source,
                    "location": "sentence",
                    "sentence": sentence["text"]
                })

                break

    symbolic = [(keyword, pattern) for match_type, keyword, pattern in keywords if match_type == "symbolic"]
    if symbolic and formulas:
        sentences_by_id = {s["id"]: s for s in sentences}
        for formula in formulas:
            if formula["type"] != "display":
                continue
            symbols, defined = _formula_symbols(formula["latex"])
            for keyword, pattern in symbolic:
                if pattern.search(symbols):
                    results.append(formula_context(formula, sentences_by_id, keyword, "symbolic", source,
                                                   defines=bool(pattern.search(defined))))
                    break

    return results


def merge_contexts(*context_lists):
    """Combine context lists, keeping the first entry for each sentence or formula.

    Order: sentences, then formulas that define the variable, then formulas that only use it.
    """
    seen = set()
    merged = []
    for contexts in context_lists:
        for context in contexts:
            key = (context["location"], context.get("formula_id") or context["sentence_id"])
            if key not in seen:
                seen.add(key)
                merged.append(context)
    return sorted(merged, key=lambda c: (c["location"] != "sentence", not c.get("defines", False)))


def parent_symbols(variable_name):
    """Names of the whole current a split CellML variable belongs to, most specific first.

    i_Ca_L_Na_ds -> ["i_Ca_L_Na", "i_Ca_L"],  i_NaCa_cyt -> ["i_NaCa"],  i_b_Na -> [] ("i_b" is too generic)
    """
    parts = variable_name.split("_")
    parents = []
    while len(parts) > 2 and (parts[-1] in COMPARTMENT_SUFFIXES or parts[-1] in ION_SUFFIXES):
        parts = parts[:-1]
        if len("".join(parts[1:])) >= 3:
            parents.append("_".join(parts))
    return parents
