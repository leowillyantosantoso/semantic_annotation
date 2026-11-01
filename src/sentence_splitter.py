import re

# Abbreviations that end with a period but do not end a sentence
ABBREVIATIONS = ["Fig", "Figs", "Eq", "Eqs", "Eqn", "Eqns", "Ref", "Refs", "et al", "i.e", "e.g", "cf", "vs", "approx", "No", "Vol"]

_ABBREV_PATTERN = re.compile(r"\b(" + "|".join(re.escape(a) for a in ABBREVIATIONS) + r")\.", re.IGNORECASE)
_PLACEHOLDER = "<DOT>"


def split_sentences(text):

    text = text.replace("\n", " ")

    text = re.sub(r"\s+", " ", text)

    # Protect abbreviation periods (e.g. "Fig. 1") from being treated as sentence ends
    text = _ABBREV_PATTERN.sub(lambda m: m.group(1) + _PLACEHOLDER, text)

    sentences = re.split(r'(?<=[.!?])\s+', text)

    sentences = [s.replace(_PLACEHOLDER, ".").strip() for s in sentences]

    return [s for s in sentences if len(s) > 20]
