"""
Extract text and formulas from a research paper (PDF) using LlamaParse (LlamaCloud).

LlamaParse returns one markdown document per page; formulas are written as LaTeX
($...$, $$...$$, \\(...\\), \\[...\\]). The raw result is cached as JSON next to the
stage output so re-running the pipeline does not parse (and pay for) the same PDF twice.
"""

import os
import re
import json

from src.sentence_splitter import split_sentences

# Display formulas first so their $$ delimiters are not mistaken for inline $
FORMULA_PATTERNS = [
    ("display", re.compile(r"\$\$(.+?)\$\$", re.DOTALL)),
    ("display", re.compile(r"\\\[(.+?)\\\]", re.DOTALL)),
    ("display", re.compile(r"\\begin\{(equation|align|eqnarray|gather|multline)\*?\}(.+?)\\end\{\1\*?\}", re.DOTALL)),
    ("inline", re.compile(r"(?<![\\$])\$(?!\$)(.+?)(?<![\\$])\$(?!\$)", re.DOTALL)),
    ("inline", re.compile(r"\\\((.+?)\\\)", re.DOTALL)),
]


def parse_pdf_with_llamaparse(pdf_path, api_key, tier="agentic"):
    """Send a PDF to LlamaParse and return a list of pages: [{"page": n, "markdown": str}]."""
    from llama_cloud import LlamaCloud

    if not api_key:
        raise ValueError(
            "LlamaParse API key is missing. Set LLAMA_CLOUD_API_KEY in config.py "
            "or as an environment variable."
        )

    client = LlamaCloud(api_key=api_key)
    with open(pdf_path, "rb") as f:
        result = client.parsing.parse(
            tier=tier,
            version="latest",
            upload_file=f,
            expand=["markdown"],
        )

    if result.job.status != "COMPLETED":
        raise RuntimeError(f"LlamaParse job {result.job.id} ended with status {result.job.status}: {result.job.error_message}")

    pages = []
    for page in result.markdown.pages:
        if page.success:
            pages.append({"page": page.page_number, "markdown": page.markdown})
        else:
            print(f"    WARNING: LlamaParse failed on page {page.page_number}: {page.error}")
    return pages


def load_or_parse_pdf(pdf_path, cache_path, api_key, tier="agentic"):
    """Return parsed pages, using the cached LlamaParse result when it exists."""
    if os.path.isfile(cache_path):
        print(f"    [Cache] Using cached LlamaParse result: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    print(f"    Sending {os.path.basename(pdf_path)} to LlamaParse (tier={tier}). This can take a few minutes...")
    pages = parse_pdf_with_llamaparse(pdf_path, api_key, tier)

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(pages, f, indent=2, ensure_ascii=False)
    return pages


def extract_formulas(markdown, first_id=1):
    """Find LaTeX formulas in a markdown string.

    Display formulas are replaced by an "[EQ n]" marker so sentences still point to them;
    inline formulas stay in the sentence text so the context remains readable.

    Returns:
        (formulas, text) where formulas is a list of {"id", "type", "latex"}
    """
    formulas = []

    def take(kind):
        def replace(match):
            latex = match.group(match.lastindex).strip()
            if not latex:
                return " "
            formula_id = first_id + len(formulas)
            formulas.append({"id": formula_id, "type": kind, "latex": latex})
            return f" {latex} " if kind == "inline" else f" [EQ {formula_id}] "
        return replace

    text = markdown
    for kind, pattern in FORMULA_PATTERNS:
        text = pattern.sub(take(kind), text)
    return formulas, text


EQ_MARKER = re.compile(r"\[EQ (\d+)\]")
# Equation number printed next to a display formula: "[EQ 52] [29]" or "[EQ 123] (3a)"
EQ_LABEL = re.compile(r"\[EQ (\d+)\]\s*[\[(](\d+[a-z]?)[\])]")


def _markdown_to_plain(text):
    """Strip markdown markup that would otherwise pollute sentences."""
    text = re.sub(r"<[^>]+>", " ", text)                      # html tags (e.g. tables)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)      # headings
    text = re.sub(r"^\s*\|.*\|\s*$", " ", text, flags=re.M)     # markdown table rows
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)          # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)       # links -> link text
    text = re.sub(r"[*`]{1,3}", "", text)                      # bold / italic / code (keep _ for symbols like i_Na)
    return text


def extract_paper(pages):
    """Split parsed pages into sentences and formulas, keeping page numbers.

    Each display formula also gets its printed equation number ("label") and the ids of the
    sentences around it ("sentence_before", "sentence_after"; the same id when the formula
    sits inside a sentence), so a formula match can be shown with its surrounding text.

    Returns:
        {"total_pages", "total_sentences", "total_formulas", "sentences", "formulas"}
    """
    sentences = []
    formulas = []
    waiting = []  # formulas still looking for the next sentence

    for page in pages:
        page_formulas, text = extract_formulas(page["markdown"], first_id=len(formulas) + 1)
        labels = {int(n): label for n, label in EQ_LABEL.findall(text)}
        for formula in page_formulas:
            formulas.append({"page": page["page"], **formula, "label": labels.get(formula["id"]),
                             "sentence_before": None, "sentence_after": None})

        # Split per paragraph so headings and captions do not merge into the next sentence
        for paragraph in re.split(r"\n\s*\n", _markdown_to_plain(text)):
            paragraph_sentences = split_sentences(paragraph)
            inside = set()
            for sentence in paragraph_sentences:
                sentence_id = len(sentences) + 1
                sentences.append({"id": sentence_id, "page": page["page"], "text": sentence})
                for formula_id in waiting:
                    formulas[formula_id - 1]["sentence_after"] = sentence_id
                waiting = []
                for n in EQ_MARKER.findall(sentence):
                    formula = formulas[int(n) - 1]
                    formula["sentence_before"] = formula["sentence_after"] = sentence_id
                    inside.add(int(n))
            # Markers in fragments too short to be sentences (e.g. "[EQ 52] [29]")
            for n in EQ_MARKER.findall(paragraph):
                if int(n) not in inside:
                    before = len(sentences) - len(paragraph_sentences)
                    formulas[int(n) - 1]["sentence_before"] = before or None
                    waiting.append(int(n))

    return {
        "total_pages": len(pages),
        "total_sentences": len(sentences),
        "total_formulas": len(formulas),
        "sentences": sentences,
        "formulas": formulas,
    }
