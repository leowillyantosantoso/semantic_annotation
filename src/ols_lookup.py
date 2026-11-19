"""
Stage 4: find ontology terms for the Stage 3 keywords with the EBI Ontology Lookup Service (OLS4).

Only exact matches are accepted: the name or a keyword must equal a term's label or one of its
synonyms (ignoring case). When no keyword matches, the term is left empty and the best OLS search results
are kept as candidates for manual review, because OLS ranks partial matches such as
"regulation of ..." too high to pick one automatically.
"""

import json
import os
import time

import requests

OLS_SEARCH = "https://www.ebi.ac.uk/ols4/api/search"
MAX_CANDIDATES = 5
SKIP = {"none", "unknown", "diffusion", ""}  # names that have no ontology term


def _normalize(text):
    return " ".join(text.lower().split())


def _term_id(doc):
    """OLS gives FMA ids as "fma70022"; use the usual "FMA:70022"."""
    obo_id = doc.get("obo_id") or doc.get("short_form", "")
    if obo_id.lower().startswith("fma") and ":" not in obo_id:
        return "FMA:" + obo_id[3:]
    return obo_id.replace("_", ":")


def _queries(name, keywords, kind, ontologies):
    """(query, ontology, from_name) to try, most wanted first.

    Keyword by keyword (the name first, then the LLM's order, most specific first), each in every
    ontology in order of preference, so a specific keyword in a later ontology (GO "terminal
    cisterna") beats a broader keyword in an earlier one (FMA "Sarcoplasmic reticulum").
    """
    words = list(dict.fromkeys([name] + keywords))  # no duplicates
    if kind == "mediator":
        # A mediator is a physical entity: try every keyword as a GO protein complex before
        # accepting an "... activity" term
        words = [f"{w} complex" for w in words] + words
    queries = []
    for word in words:
        for ontology in ontologies:
            queries.append((word, ontology))
            if ontology == "fma":
                # FMA names many compartments "Portion of ..." (e.g. "Portion of cytosol")
                queries.append((f"portion of {word}", ontology))
    from_name = {name, f"{name} complex", f"portion of {name}"}
    return [(query, ontology, query in from_name) for query, ontology in queries]


class OLSLookup:
    """OLS search with a JSON cache on disk, shared by all models."""

    def __init__(self, cache_path, ontologies):
        self.cache_path = cache_path
        self.ontologies = ontologies  # kind -> ontologies in order of preference
        self.cache = {}
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                self.cache = json.load(f)
        self.session = requests.Session()
        self.new_queries = 0

    def save(self):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=1, ensure_ascii=False)

    def _search(self, query, ontology, exact):
        key = f"{ontology}|{'exact' if exact else 'search'}|{_normalize(query)}"
        if key not in self.cache:
            params = {"q": query, "ontology": ontology, "exact": str(exact).lower(), "rows": 25,
                      "type": "class", "queryFields": "label,synonym",
                      "fieldList": "obo_id,short_form,iri,label,synonym,ontology_name"}
            for attempt in range(3):
                try:
                    response = self.session.get(OLS_SEARCH, params=params, timeout=30)
                    response.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 2:
                        raise
                    time.sleep(2 * (attempt + 1))
            self.cache[key] = [
                {"id": _term_id(doc), "label": doc["label"], "synonyms": doc.get("synonym", []),
                 "ontology": doc["ontology_name"], "iri": doc["iri"]}
                for doc in response.json()["response"]["docs"]
            ]
            self.new_queries += 1
        return self.cache[key]

    def _exact(self, query, ontology):
        """The first search result whose label or a synonym equals the query, or None."""
        wanted = _normalize(query)
        # exact=true misses some terms (e.g. GO "extracellular space"), so also check a normal search
        for exact in (True, False):
            for term in self._search(query, ontology, exact):
                if _normalize(term["label"]) == wanted:
                    return term, "label"
                if wanted in (_normalize(s) for s in term["synonyms"]):
                    return term, "synonym"
        return None, None

    def find(self, name, keywords, kind):
        """Ontology term for one chemical, compartment or mediator.

        Args:
            name: The Stage 3 name (e.g. "cytosol"); tried first
            keywords: The Stage 3 search keywords, in the LLM's order
            kind: "chemical", "compartment" or "mediator"

        Returns:
            {"name", "term", "match", "matched_keyword", "matched_on", "candidates"}. "match" is
            "name" when the name itself was found, "keyword" when only one of the keywords was
            (possibly a broader term, e.g. "cytosol" for the dyadic space), or None. When nothing
            matched, "term" is None and "candidates" holds the best OLS results.
        """
        result = {"name": name, "term": None, "match": None, "matched_keyword": None, "matched_on": None,
                  "candidates": []}
        if _normalize(name) in SKIP:
            return result

        for query, ontology, from_name in _queries(name, keywords, kind, self.ontologies[kind]):
            term, matched_on = self._exact(query, ontology)
            if term:
                return {**result, "term": _public(term), "match": "name" if from_name else "keyword",
                        "matched_keyword": query, "matched_on": matched_on}

        # Nothing matched: keep the top search results of the name and keywords for manual review
        candidates = {}
        for word in dict.fromkeys([name] + keywords):
            for ontology in self.ontologies[kind]:
                for term in self._search(word, ontology, exact=False)[:3]:
                    if "regulation of" not in term["label"].lower():
                        candidates.setdefault(term["id"], _public(term))
        result["candidates"] = list(candidates.values())[:MAX_CANDIDATES]
        return result


def _public(term):
    return {"id": term["id"], "label": term["label"], "ontology": term["ontology"], "iri": term["iri"]}
