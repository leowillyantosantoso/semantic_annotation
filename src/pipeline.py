"""
Pipeline orchestrator. Each stage reads its inputs from files and writes its result
as JSON under output/<model_name>/ so every stage can be verified on its own.

Stage 1: Input acquisition and preprocessing
    - CellML  -> variables (component, name, unit, formula) via libcellml
    - PDF     -> sentences and formulas via LlamaParse

Stage 2: Synonym generation and context search
    - LLM     -> symbolic and textual synonyms per variable (prompt.py; Claude, OpenAI or Ollama)
    - Paper   -> sentences and equations that mention any synonym (contexts)
    - Equation matching -> CellML equations linked to paper equations (equation_matching.py)

Stage 3: Biological process inference
    - LLM     -> per variable: role, mediator and participants (chemical, source, sink), each with
                 ontology search keywords for Stage 4 (prompt.py PROCESS_PROMPT_TEMPLATE)

Stage 4: Ontology lookup
    - OLS     -> ChEBI / FMA / GO term for every chemical, compartment and mediator (ols_lookup.py)
"""

import os
import re
import json

from config import (DATA_DIR, OUTPUT_DIR, LLAMA_CLOUD_API_KEY, LLAMAPARSE_TIER, TARGET_OPB, LLM_PROVIDER,
                    INFER_MISSING_CONTEXT, ONTOLOGIES, OLS_CACHE, ENERGY_DIFFERENTIAL_OPB)
from src.cellml_reader import read_cellml, read_equations, read_energy_differentials
from src.pdf_reader import load_or_parse_pdf, extract_paper
from src.llm_synonym import generate_synonyms, describe_from_formula, annotate_process, annotate_energy_differential
from src.context_search import search_context, formula_context, merge_contexts, parent_symbols
from src.equation_matching import match_equations
from src.ols_lookup import OLSLookup, SKIP


def model_output_dir(model):
    name = os.path.splitext(model["cellml"])[0]
    return os.path.join(OUTPUT_DIR, name)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  -> {os.path.relpath(path)}")


def run_stage1(model, skip_pdf=False):
    """STAGE 1: Extract CellML variables and paper text/formulas.

    Writes:
        stage1_variables.json  - variables with allowed units (+ empty synonyms/contexts for Stage 2)
        stage1_energy_differentials.json - voltages that drive a gating variable (ENERGY_DIFFERENTIAL_OPB)
        stage1_paper.json      - sentences and formulas from the paper, with page numbers
        llamaparse_raw.json    - raw per-page markdown from LlamaParse (cache)
    """
    out_dir = model_output_dir(model)
    cellml_path = os.path.join(DATA_DIR, model["cellml"])
    pdf_path = os.path.join(DATA_DIR, model["pdf"])

    print(f"[{model['cellml']}] STAGE 1: Reading variables from CellML...")
    variables = read_cellml(cellml_path)
    print(f"  Found {len(variables)} variables with units compatible with {TARGET_OPB}.")
    energy_differentials = read_energy_differentials(cellml_path)
    print(f"  Found {len(energy_differentials)} voltage variables ({ENERGY_DIFFERENTIAL_OPB}) that drive a gating variable.")

    # 1b. Paper text and formulas
    paper = None
    if skip_pdf:
        print("  Skipping PDF extraction (--skip-pdf).")
    elif not os.path.isfile(pdf_path):
        print(f"  WARNING: PDF not found: {pdf_path}. Skipping paper extraction.")
    else:
        print(f"[{model['pdf']}] STAGE 1: Extracting text and formulas from paper...")
        pages = load_or_parse_pdf(
            pdf_path,
            cache_path=os.path.join(out_dir, "llamaparse_raw.json"),
            api_key=LLAMA_CLOUD_API_KEY,
            tier=LLAMAPARSE_TIER,
        )
        paper = extract_paper(pages)
        print(f"  {paper['total_pages']} pages, {paper['total_sentences']} sentences, {paper['total_formulas']} formulas.")

    # Output: variables (format is extended by Stage 2 with synonyms and contexts)
    stage1 = {
        "paper_title": model["paper_title"],
        "cellml_file": model["cellml"],
        "pdf_file": model["pdf"],
        "total_variables": len(variables),
        "results": [
            {
                "component": var["component"],
                "variable": var["variable"],
                "unit": var["unit"],
                "compatible_with": var["compatible_with"],
                "formula": var["formula"],
                "formula_component": var["formula_component"],
                "synonyms": {"symbolic": [], "textual": []},
                "contexts": []
            }
            for var in variables
        ]
    }
    _write_json(os.path.join(out_dir, "stage1_variables.json"), stage1)

    _write_json(os.path.join(out_dir, "stage1_energy_differentials.json"), {
        "paper_title": model["paper_title"],
        "cellml_file": model["cellml"],
        "pdf_file": model["pdf"],
        "opb": ENERGY_DIFFERENTIAL_OPB,
        "total_variables": len(energy_differentials),
        "results": energy_differentials,
    })

    if paper is not None:
        _write_json(os.path.join(out_dir, "stage1_paper.json"), {
            "paper_title": model["paper_title"],
            "pdf_file": model["pdf"],
            "parser": f"llamaparse ({LLAMAPARSE_TIER})",
            **paper
        })

    print("  STAGE 1 completed.")
    return stage1, paper


def _save_cache(path, cache):
    # Saved after every variable so an interrupted run keeps its progress
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def run_stage2(model):
    """STAGE 2: Generate synonyms with the LLM (LLM_PROVIDER) and find matching sentences in the paper.

    Contexts are collected in this order; each step only adds sentences/formulas not found yet:
        1. LLM synonyms        -> sentences and display formulas            (source "llm")
        2. Equation matching   -> the paper equation that matches the CellML
                                  equation, plus sentences/formulas with the
                                  paper symbol learned from the matching    (source "equation")
        3. Parent current      -> only if nothing was found: i_Ca_L_Na_ds is
                                  searched as i_Ca_L_Na and i_Ca_L          (source "parent")
        4. Inference           -> only if still nothing: the LLM describes the variable from its
                                  formula. Stored as "inferred_description", NOT as a context,
                                  because it is not evidence from the paper (INFER_MISSING_CONTEXT)

    Reads:
        stage1_variables.json, stage1_energy_differentials.json (if present), stage1_paper.json,
        the CellML file
    Writes:
        stage2_variables.json  - stage 1 variables with synonyms, paper_symbols and contexts
        stage2_energy_differentials.json - the same for the energy differentials, plus
                                           gate_contexts: evidence for the gate each one drives
                                           (see _gate_contexts)
        stage2_equation_matches.json - CellML equation -> paper equation links and symbol mappings
        <provider>_synonyms.json - the LLM's synonyms per variable, e.g. claude_synonyms.json
                                   (cache; delete to regenerate)
        <provider>_inferred.json - the LLM's descriptions for variables without context (cache)
    """
    out_dir = model_output_dir(model)
    variables_path = os.path.join(out_dir, "stage1_variables.json")
    paper_path = os.path.join(out_dir, "stage1_paper.json")
    cache_path = os.path.join(out_dir, f"{LLM_PROVIDER}_synonyms.json")
    inferred_path = os.path.join(out_dir, f"{LLM_PROVIDER}_inferred.json")

    for path in (variables_path, paper_path):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{os.path.relpath(path)} not found. Run Stage 1 (with the PDF) first.")

    stage1 = _read_json(variables_path)
    paper = _read_json(paper_path)
    sentences = paper["sentences"]
    formulas = paper["formulas"]
    if formulas and "sentence_before" not in formulas[0]:
        print("  WARNING: stage1_paper.json is from an older version; re-run Stage 1 to show formulas with their surrounding sentences.")
    cache = _read_json(cache_path) if os.path.isfile(cache_path) else {}
    inferred_cache = _read_json(inferred_path) if os.path.isfile(inferred_path) else {}

    print(f"[{model['cellml']}] STAGE 2: Matching CellML equations with the paper's equations...")
    cellml_equations = read_equations(os.path.join(DATA_DIR, model["cellml"]))
    matches = match_equations(cellml_equations, formulas)
    print(f"  {len(matches['equations'])} CellML equations linked to {matches['parsed_paper_equations']} parsed paper equations.")
    _write_json(os.path.join(out_dir, "stage2_equation_matches.json"), matches)

    print(f"[{model['cellml']}] STAGE 2: Generating synonyms with {LLM_PROVIDER} and searching the paper...")
    caches = (cache, cache_path, inferred_cache, inferred_path)
    stage2 = _stage2_contexts(stage1, model, paper, matches, caches)
    _write_json(os.path.join(out_dir, "stage2_variables.json"), stage2)

    # Energy differentials: every gate's copy of V is the same quantity, so synonyms and the
    # inferred description are generated once for the defining variable (e.g. membrane/V)
    energy_path = os.path.join(out_dir, "stage1_energy_differentials.json")
    if os.path.isfile(energy_path):
        energy = _read_json(energy_path)
        print(f"[{model['cellml']}] STAGE 2: Energy differentials ({energy.get('opb')})...")
        energy2 = _stage2_contexts(energy, model, paper, matches, caches, by_definition=True)
        if any("gate_variables" not in var for var in energy2["results"]):
            print("  WARNING: stage1_energy_differentials.json is from an older version; re-run Stage 1 for gate evidence.")
        else:
            print(f"[{model['cellml']}] STAGE 2: Gate-specific evidence for the energy differentials...")
            for var in energy2["results"]:
                var["gate_contexts"] = _gate_contexts(var, model, paper, matches, cellml_equations, caches)
                sources = sorted({c["source"] for c in var["gate_contexts"]})
                print(f"  {var['component']}/{var['variable']} (drives {', '.join(var['drives'])}): "
                      f"{len(var['gate_contexts'])} gate contexts {sources if sources else ''}")
        _write_json(os.path.join(out_dir, "stage2_energy_differentials.json"), energy2)
    else:
        print("  No stage1_energy_differentials.json; re-run Stage 1 to annotate energy differentials.")

    print("  STAGE 2 completed.")
    return stage2


def _gate_contexts(var, model, paper, matches, cellml_equations, caches):
    """Paper evidence specific to one energy differential: the gate it drives and the current
    that gate controls. Every gate's copy of V shares the general membrane-potential contexts,
    so only these tell the copies apart.

    Sources, each adding only sentences/formulas not found yet:
        gate_equation      - paper equations matched to the gate's CellML equations (dm/dt, alpha_m, ...)
        controlled_current - paper equation matched to the current the gate feeds (i_Na = ...)
        gate_symbol        - sentences/formulas with the paper symbols of the gate variables
        gate_llm           - sentences matching LLM synonyms of the gating variable
                             (cached as <component>/<gating variable>, e.g. sodium_channel_m_gate/m)
    """
    cache, cache_path, _, _ = caches
    sentences, formulas = paper["sentences"], paper["formulas"]
    formulas_by_id = {f["id"]: f for f in formulas}
    sentences_by_id = {s["id"]: s for s in sentences}
    comp = var["component"]

    def linked(component, names, source):
        return [
            formula_context(formulas_by_id[link["formula_id"]], sentences_by_id, link["cellml_formula"],
                            "structure", source, defines=True, cellml_variable=link["variable"],
                            similarity=link["similarity"], confidence=link["confidence"])
            for link in matches["equations"]
            if link["component"] == component and link["variable"] in names
        ]

    gate_equations = linked(comp, set(var["gate_variables"]), "gate_equation")
    current_equations = []
    for current in var["controls"]:
        current_comp, current_name = current.rsplit(".", 1)
        current_equations += linked(current_comp, {current_name}, "controlled_current")

    symbols = [s["symbol"] for name in var["gate_variables"] for s in matches["symbols"].get(name, [])]
    by_symbol = search_context(sentences, {"symbolic": symbols}, formulas, source="gate_symbol")

    by_synonym = []
    for gate in var["drives"]:
        key = f"{comp}/{gate}"
        if key not in cache:
            ode = next((eq["text"] for eq in cellml_equations
                        if eq["component"] == comp and eq["text"].startswith(f"d({gate})/")), None)
            cache[key] = generate_synonyms({"variable": gate, "component": comp, "unit": "dimensionless",
                                            "formula": ode}, model["paper_title"])
            _save_cache(cache_path, cache)
        by_synonym += search_context(sentences, cache[key], formulas, source="gate_llm")

    return merge_contexts(gate_equations, current_equations, by_symbol, by_synonym)


def _stage2_contexts(stage1, model, paper, matches, caches, by_definition=False):
    """Synonyms and paper contexts (steps 1-4 of run_stage2) for every variable in `stage1`.

    by_definition: cache synonyms/inferred descriptions per defining variable
    (formula_component/variable) instead of per component copy.
    """
    cache, cache_path, inferred_cache, inferred_path = caches
    sentences = paper["sentences"]
    formulas = paper["formulas"]
    formulas_by_id = {f["id"]: f for f in formulas}
    sentences_by_id = {s["id"]: s for s in sentences}

    results = []
    for i, var in enumerate(stage1["results"], start=1):
        label = f"  ({i}/{stage1['total_variables']}) {var['component']}/{var['variable']}"
        if by_definition and var.get("formula_component"):
            key = f"{var['formula_component']}/{var['variable']}"
            prompt_var = {**var, "component": var["formula_component"]}
        else:
            key = f"{var['component']}/{var['variable']}"
            prompt_var = var

        if key in cache:
            synonyms = cache[key]
            label += " [cached synonyms]"
        else:
            synonyms = generate_synonyms(prompt_var, model["paper_title"])
            cache[key] = synonyms
            _save_cache(cache_path, cache)

        # 1. LLM synonyms
        contexts = search_context(sentences, synonyms, formulas)

        # 2. Equation matching
        paper_symbols = matches["symbols"].get(var["variable"], [])
        linked = [
            formula_context(formulas_by_id[link["formula_id"]], sentences_by_id, link["cellml_formula"],
                            "structure", "equation", defines=True,
                            similarity=link["similarity"], confidence=link["confidence"])
            for link in matches["equations"] if link["variable"] == var["variable"]
        ]
        found = search_context(sentences, {"symbolic": [s["symbol"] for s in paper_symbols]}, formulas, source="equation")
        contexts = merge_contexts(contexts, linked, found)

        # 3. Parent current
        if not contexts:
            contexts = merge_contexts(
                search_context(sentences, {"symbolic": parent_symbols(var["variable"])}, formulas, source="parent"))

        result = {**var, "synonyms": synonyms, "paper_symbols": paper_symbols, "contexts": contexts}
        sources = sorted({c["source"] for c in contexts})

        # 4. Inference from the formula
        if not contexts and INFER_MISSING_CONTEXT:
            if key not in inferred_cache:
                inferred_cache[key] = describe_from_formula(prompt_var, model["paper_title"])
                _save_cache(inferred_path, inferred_cache)
            result["inferred_description"] = {**inferred_cache[key], "source": "inferred_from_formula"}
            sources = ["inferred"]

        print(f"{label}: {len(synonyms['symbolic'])} symbolic, {len(synonyms['textual'])} textual, "
              f"{len(contexts)} contexts {sources if sources else ''}")
        results.append(result)

    with_context = sum(1 for r in results if r["contexts"])
    print(f"  {with_context}/{len(results)} variables have contexts in the paper.")
    return {**stage1, "results": results}


MAX_EVIDENCE = 8          # evidence items sent to the LLM per variable
MAX_ENERGY_EVIDENCE = 12  # per energy differential: gate-specific items first, then general ones
MAX_GATE_EQUATIONS = 12   # CellML gate equations sent to the LLM per energy differential
MAX_EVIDENCE_CHARS = 600  # long items (e.g. the full membrane equation) are cut
MAX_USED_IN = 6           # CellML equations that use the variable, sent to the LLM
CONSTANT = re.compile(r"^\s*\w+\s*=\s*[-+]?[\d.]+(e[-+]?\d+)?\s*$", re.IGNORECASE)


def _used_in(variable, equations):
    """CellML equations that use the variable (not the one defining it), one per line."""
    pattern = re.compile(rf"\b{re.escape(variable)}\b")
    found = []
    for eq in equations:
        lhs, _, rhs = eq["text"].partition("=")
        if lhs.strip() != variable and pattern.search(rhs) and eq["text"] not in found:
            found.append(eq["text"])
    lines = [f"  {text[:MAX_EVIDENCE_CHARS]}" for text in found[:MAX_USED_IN]]
    return "\n" + "\n".join(lines) if lines else "none"


def _evidence(var, limit=MAX_EVIDENCE):
    """Number the variable's contexts for the Stage 3 prompt; defining equations first.

    Returns:
        (text for the prompt, list of the items so the LLM's evidence numbers can be resolved)
    """
    contexts = sorted(var["contexts"], key=lambda c: not c.get("defines"))[:limit]
    items, lines = [], []
    for n, ctx in enumerate(contexts, start=1):
        text = ctx["sentence"]
        nearby = " ".join(s["sentence"] for s in ctx.get("nearby_sentences", []))
        if nearby:
            text += f" (nearby text: {nearby})"
        if len(text) > MAX_EVIDENCE_CHARS:
            text = text[:MAX_EVIDENCE_CHARS] + " ..."
        lines.append(f"[{n}] (page {ctx['page']}) {text}")
        items.append({"page": ctx["page"], "sentence_id": ctx.get("sentence_id"),
                      "formula_id": ctx.get("formula_id"), "text": ctx["sentence"]})

    if not lines and var.get("inferred_description"):
        lines.append("No sentence in the paper. Description inferred by an LLM from the formula (not evidence): "
                     + var["inferred_description"]["description"])
    return "\n".join(lines) or "None found in the paper.", items


def _gate_equations(var, equations):
    """CellML equations of the gate an energy differential drives (dm/dt, alpha_m, ...), one per line."""
    names = set(var.get("gate_variables", []))
    found = []
    for eq in equations:
        lhs = eq["text"].partition("=")[0].strip()
        defined = lhs[2:].split(")")[0] if lhs.startswith("d(") else lhs
        if eq["component"] == var["component"] and defined in names and eq["text"] not in found:
            found.append(eq["text"])
    lines = [f"  {text[:MAX_EVIDENCE_CHARS]}" for text in found[:MAX_GATE_EQUATIONS]]
    return "\n" + "\n".join(lines) if lines else "none"


def _stage3_energy_differentials(model, out_dir, equations):
    """Stage 3 for stage2_energy_differentials.json: source and sink compartment of each
    gating voltage (no mediator, no chemical). One LLM call per component copy, because each
    copy has its own gate evidence. Cached in <provider>_energy.json."""
    energy_path = os.path.join(out_dir, "stage2_energy_differentials.json")
    if not os.path.isfile(energy_path):
        print("  No stage2_energy_differentials.json; run Stage 2 to annotate energy differentials.")
        return None

    energy = _read_json(energy_path)
    cache_path = os.path.join(out_dir, f"{LLM_PROVIDER}_energy.json")
    cache = _read_json(cache_path) if os.path.isfile(cache_path) else {}

    print(f"[{model['cellml']}] STAGE 3: Energy differentials ({energy.get('opb')}) with {LLM_PROVIDER}...")
    results, failed = [], []
    for i, var in enumerate(energy["results"], start=1):
        key = f"{var['component']}/{var['variable']}"
        label = f"  ({i}/{energy['total_variables']}) {key}"
        # Gate-specific evidence first (equations of this gate, then sentences), then general items
        gate_first = {**var, "contexts": var.get("gate_contexts", []) + var["contexts"]}
        evidence_text, evidence_items = _evidence(gate_first, limit=MAX_ENERGY_EVIDENCE)

        if key in cache:
            label += " [cached]"
        else:
            try:
                cache[key] = annotate_energy_differential(var, energy["paper_title"], evidence_text,
                                                          _gate_equations(var, equations))
            except Exception as e:
                print(f"{label}: SKIPPED ({e})")
                failed.append(key)
                continue
            _save_cache(cache_path, cache)
        answer = cache[key]

        results.append({
            "component": var["component"],
            "variable": var["variable"],
            "unit": var["unit"],
            "formula_component": var.get("formula_component") or var["component"],
            "variable_role": "energy_differential",
            "drives": var.get("drives", []),
            "controls": var.get("controls", []),
            "source": answer["source"],
            "source_keywords": answer["source_keywords"],
            "sink": answer["sink"],
            "sink_keywords": answer["sink_keywords"],
            "confidence": answer["confidence"],
            "rationale": answer["rationale"],
            "evidence": [evidence_items[n - 1] for n in answer["evidence"] if 1 <= n <= len(evidence_items)],
        })
        print(f"{label}: {answer['source']} - {answer['sink']} ({answer['confidence']})")

    if failed:
        print(f"  WARNING: {len(failed)} energy differentials skipped (run Stage 3 again to retry): {', '.join(failed)}")

    stage3 = {**{k: energy[k] for k in ("paper_title", "cellml_file", "pdf_file", "opb", "total_variables")},
              "failed": failed, "results": results}
    _write_json(os.path.join(out_dir, "stage3_energy_differentials.json"), stage3)
    return stage3


def run_stage3(model):
    """STAGE 3: Infer the biological process of each variable with the LLM (LLM_PROVIDER).

    One LLM call per defining variable (formula_component/variable): connected copies of a
    variable in other components reuse its annotation. The prompt also gets the CellML equations
    that use the variable, and a current set to a plain number is a "parameter" (unless it is a
    stimulus). A variable whose LLM call fails is listed
    in "failed" and retried on the next run.

    Reads:
        stage2_variables.json, the CellML file
    Writes:
        stage3_annotations.json - per variable: role, mediator, participants (chemical, source,
                                  sink) with ontology search keywords, rationale, evidence used
        <provider>_process.json - the LLM's answers per defining variable (cache; delete to regenerate)
        stage3_energy_differentials.json - per energy differential (from
                                  stage2_energy_differentials.json): source and sink compartment,
                                  with voltage = potential(source) - potential(sink); no mediator
        <provider>_energy.json  - the LLM's answers per energy differential copy (cache)
    """
    out_dir = model_output_dir(model)
    variables_path = os.path.join(out_dir, "stage2_variables.json")
    cache_path = os.path.join(out_dir, f"{LLM_PROVIDER}_process.json")

    if not os.path.isfile(variables_path):
        raise FileNotFoundError(f"{os.path.relpath(variables_path)} not found. Run Stage 2 first.")

    stage2 = _read_json(variables_path)
    cache = _read_json(cache_path) if os.path.isfile(cache_path) else {}
    equations = read_equations(os.path.join(DATA_DIR, model["cellml"]))

    print(f"[{model['cellml']}] STAGE 3: Inferring biological processes with {LLM_PROVIDER}...")
    results, failed = [], []
    for i, var in enumerate(stage2["results"], start=1):
        defined_in = var.get("formula_component") or var["component"]
        key = f"{defined_in}/{var['variable']}"
        label = f"  ({i}/{stage2['total_variables']}) {var['component']}/{var['variable']}"
        evidence_text, evidence_items = _evidence(var)

        if key in cache:
            label += " [cached]"
        else:
            # A refusal or error skips only this variable; it is not cached, so the next run retries it
            try:
                cache[key] = annotate_process(var, stage2["paper_title"], evidence_text,
                                              _used_in(var["variable"], equations))
            except Exception as e:
                print(f"{label}: SKIPPED ({e})")
                failed.append(f"{var['component']}/{var['variable']}")
                continue
            _save_cache(cache_path, cache)
        answer = cache[key]
        # A current set to a plain number is a parameter (a stimulus amplitude stays a stimulus)
        if CONSTANT.match(var.get("formula") or "") and answer["variable_role"] != "stimulus_current":
            answer = {**answer, "variable_role": "parameter"}

        results.append({
            "component": var["component"],
            "variable": var["variable"],
            "unit": var["unit"],
            "formula_component": defined_in,
            "variable_role": answer["variable_role"],
            "mediator": answer["mediator"],
            "mediator_keywords": answer["mediator_keywords"],
            "participants": answer["participants"],
            "confidence": answer["confidence"],
            "rationale": answer["rationale"],
            "evidence": [evidence_items[n - 1] for n in answer["evidence"] if 1 <= n <= len(evidence_items)],
        })

        flows = ", ".join(f"{p['chemical']}: {p['source']} -> {p['sink']}" for p in answer["participants"])
        print(f"{label}: {answer['variable_role']}, {answer['mediator']} [{flows}] ({answer['confidence']})")

    if failed:
        print(f"  WARNING: {len(failed)} variables skipped (run Stage 3 again to retry): {', '.join(failed)}")

    stage3 = {**{k: stage2[k] for k in ("paper_title", "cellml_file", "pdf_file", "total_variables")},
              "failed": failed, "results": results}
    _write_json(os.path.join(out_dir, "stage3_annotations.json"), stage3)

    _stage3_energy_differentials(model, out_dir, equations)
    print("  STAGE 3 completed.")
    return stage3


def _show_term(x):
    """Term id for the console: "~" = found through a keyword only, "?" = not found."""
    if not x["term"]:
        return f"{x['name']}?"
    return x["term"]["id"] + ("" if x["match"] == "name" else "~")


def run_stage4(model):
    """STAGE 4: Look up ontology terms for the Stage 3 annotations with OLS.

    Chemicals are searched in ChEBI, compartments in FMA then GO, mediators in GO (ONTOLOGIES in
    config.py). Only a name or keyword equal to a term's label or synonym is accepted; otherwise
    "term" is null and "candidates" lists OLS's best results for manual review. A name gets one
    term per model: the keywords of all its uses are merged. "match" says whether the name itself
    ("name") or only a keyword ("keyword", possibly a broader term) was found.

    Reads:
        stage3_annotations.json
    Writes:
        stage4_annotations.json - stage 3 annotation with a "term" (id, label, ontology, iri) for
                                  the mediator and for every participant's chemical, source and sink
        stage4_energy_differentials.json - stage3_energy_differentials.json with a "term" for the
                                  source and sink compartment (if that file exists)
        ols_cache.json          - OLS answers, shared by all models (in output/)
    """
    out_dir = model_output_dir(model)
    annotations_path = os.path.join(out_dir, "stage3_annotations.json")
    if not os.path.isfile(annotations_path):
        raise FileNotFoundError(f"{os.path.relpath(annotations_path)} not found. Run Stage 3 first.")

    stage3 = _read_json(annotations_path)
    energy_path = os.path.join(out_dir, "stage3_energy_differentials.json")
    energy = _read_json(energy_path) if os.path.isfile(energy_path) else None
    ols = OLSLookup(OLS_CACHE, ONTOLOGIES)

    # The keywords for a name differ a little per variable; merge them so a name gets one term per model
    keywords = {}
    for ann in stage3["results"]:
        keywords.setdefault(("mediator", ann["mediator"]), []).extend(ann["mediator_keywords"])
        for p in ann["participants"]:
            keywords.setdefault(("chemical", p["chemical"]), []).extend(p["chemical_keywords"])
            for side in ("source", "sink"):
                keywords.setdefault(("compartment", p[side]), []).extend(p[f"{side}_keywords"])
    # Energy differentials share the compartment names, so "cytosol" gets the same term in both files
    for ann in (energy or {}).get("results", []):
        for side in ("source", "sink"):
            keywords.setdefault(("compartment", ann[side]), []).extend(ann[f"{side}_keywords"])

    terms = {}

    def lookup(kind, name):
        if (kind, name) not in terms:
            terms[(kind, name)] = ols.find(name, list(dict.fromkeys(keywords[(kind, name)])), kind)
            ols.save()
        return terms[(kind, name)]

    print(f"[{model['cellml']}] STAGE 4: Looking up ontology terms with OLS...")
    results, found, total = [], 0, 0
    for i, ann in enumerate(stage3["results"], start=1):
        mediator = lookup("mediator", ann["mediator"])
        participants = [
            {
                "chemical": lookup("chemical", p["chemical"]),
                "source": lookup("compartment", p["source"]),
                "sink": lookup("compartment", p["sink"]),
            }
            for p in ann["participants"]
        ]

        lookups = [mediator] + [x for p in participants for x in p.values()]
        lookups = [x for x in lookups if x["name"].lower() not in SKIP]  # "none", "unknown", "diffusion"
        found += sum(1 for x in lookups if x["term"])
        total += len(lookups)

        results.append({
            **{k: ann[k] for k in ("component", "variable", "unit", "formula_component",
                                   "variable_role", "confidence")},
            "mediator": mediator,
            "participants": participants,
        })

        flows = ", ".join(f"{_show_term(p['chemical'])}: {_show_term(p['source'])} -> {_show_term(p['sink'])}"
                          for p in participants)
        print(f"  ({i}/{len(stage3['results'])}) {ann['component']}/{ann['variable']}: "
              f"{_show_term(mediator) if ann['mediator'] not in ('none', 'unknown') else ann['mediator']} [{flows}]")

    print(f"  {found}/{total} terms found ({ols.new_queries} new OLS queries). "
          f"~ = found through a keyword only, ? = not found (see candidates).")
    stage4 = {**{k: stage3[k] for k in ("paper_title", "cellml_file", "pdf_file", "total_variables")},
              "results": results}
    _write_json(os.path.join(out_dir, "stage4_annotations.json"), stage4)

    if energy is None:
        print("  No stage3_energy_differentials.json; run Stage 3 to annotate energy differentials.")
    else:
        print(f"[{model['cellml']}] STAGE 4: Energy differentials ({energy.get('opb')})...")
        energy_results, found, total = [], 0, 0
        for i, ann in enumerate(energy["results"], start=1):
            source = lookup("compartment", ann["source"])
            sink = lookup("compartment", ann["sink"])
            lookups = [x for x in (source, sink) if x["name"].lower() not in SKIP]
            found += sum(1 for x in lookups if x["term"])
            total += len(lookups)
            energy_results.append({
                **{k: ann[k] for k in ("component", "variable", "unit", "formula_component",
                                       "variable_role", "drives", "controls", "confidence")},
                "source": source,
                "sink": sink,
            })
            print(f"  ({i}/{len(energy['results'])}) {ann['component']}/{ann['variable']}: "
                  f"{_show_term(source)} - {_show_term(sink)}")
        print(f"  {found}/{total} terms found ({ols.new_queries} new OLS queries so far).")
        _write_json(os.path.join(out_dir, "stage4_energy_differentials.json"), {
            **{k: energy[k] for k in ("paper_title", "cellml_file", "pdf_file", "opb", "total_variables")},
            "results": energy_results,
        })
    print("  STAGE 4 completed.")
    return stage4
