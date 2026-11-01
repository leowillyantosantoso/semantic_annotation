# Automated Semantic Annotation for CellML Models

An automated pipeline that takes, for each curated model, a **CellML file** and its associated
**research paper (PDF)**, and produces **ontology-annotated variables** (ChEBI, GO, FMA, OPB).

Every stage writes its result as JSON in `output/<model_name>/` so each stage can be verified and
validated on its own.

---

## Pipeline

| Stage | Description | Status |
|-------|-------------|--------|
| 1. Input acquisition and preprocessing | Parse the CellML structure (variables, units, formulas) with libcellml; extract text and formulas from the paper with LlamaParse | ✅ Implemented |
| 2. Synonyms and contexts | Generate symbolic and textual synonyms per variable with an LLM — Claude, ChatGPT or a free local model through Ollama (`src/prompt.py`) — then find the paper sentences and equations that mention them, and match CellML equations to the paper's equations | ✅ Implemented |
| 3. Biological process inference | For each variable, ask the same LLM what it represents: its role, the mediator (channel, pump, exchanger) and each chemical that moves with its source and sink, plus ontology search keywords for each (`PROCESS_PROMPT_TEMPLATE` in `src/prompt.py`) | ✅ Implemented |
| 4. Ontology lookup | Find a ChEBI, FMA or GO term for every chemical, compartment and mediator from Stage 3 through the EBI Ontology Lookup Service (OLS, `src/ols_lookup.py`) | ✅ Implemented |

### Stage 1 outputs

| File | Content |
|------|---------|
| `stage1_variables.json` | Variables whose units are compatible with a charge-flow-rate unit (see [Unit selection](#unit-selection)), with component, unit, the matching baseline unit (`compatible_with`) and formula. `synonyms` and `contexts` are empty placeholders filled by Stage 2. |
| `stage1_paper.json` | Sentences and formulas (LaTeX) from the paper, with page numbers. Display formulas are replaced by `[EQ n]` in sentences, pointing to the formula with id `n`. Each formula also has its printed equation number (`label`) and the ids of the sentences around it (`sentence_before`, `sentence_after`). |
| `llamaparse_raw.json` | Raw per-page markdown returned by LlamaParse. Also used as a cache: delete it to re-parse the PDF. |

Example (`output/hodgkin_huxley/stage1_variables.json`):

```json
{
  "paper_title": "A quantitative description of membrane current and its application to conduction and excitation in nerve",
  "cellml_file": "hodgkin_huxley.cellml",
  "pdf_file": "hodgkin_huxley.pdf",
  "total_variables": 3,
  "results": [
    {
      "component": "leakage_current",
      "variable": "i_L",
      "unit": "microA_per_cm2",
      "compatible_with": "C_per_m2_s",
      "formula": "i_L = g_L * (V - E_L)",
      "formula_component": "leakage_current",
      "synonyms": { "symbolic": [], "textual": [] },
      "contexts": []
    }
  ]
}
```

`formula_component` is the component where the equation was found. For a variable that is only an
input in its component, the formula is taken from the connected component that defines it.

### Unit selection

Which variables are extracted is not hardcoded. `src/rdf_unit_cellml.ttl` maps baseline units to OPB
terms, and `src/baseline_units.cellml` defines those units. Every unit that the TTL maps to
`TARGET_OPB` in `config.py` (default `OPB_00318`, charge flow rate: `C_per_m3_s`, `C_per_m2_s`,
`C_per_m_s`, `A_per_F`, `fA`) is loaded, and a CellML variable is extracted when libcellml
(`Units.compatible`) finds its units dimensionally compatible with any of them. For example
`microA_per_cm2` → `C_per_m2_s`, `nanoA` → `fA`, `picoA_per_picoF` → `A_per_F`.
To extract other kinds of variables, add units to the two files or change `TARGET_OPB`.

### Stage 2 outputs

| File | Content |
|------|---------|
| `stage2_variables.json` | Same as `stage1_variables.json`, with `synonyms`, `paper_symbols` (paper symbols learned from equation matching) and `contexts` filled in. Variables without any context get an `inferred_description` instead (step 4 below). |
| `stage2_equation_matches.json` | Every CellML equation linked to a paper equation (with `similarity` and `confidence`), and the CellML variable → paper symbol mappings derived from them. |
| `<provider>_synonyms.json` | The LLM's synonyms per variable (`claude_synonyms.json`, `openai_synonyms.json` or `ollama_synonyms.json`). Also used as a cache: delete it to ask the LLM again. |
| `<provider>_inferred.json` | The LLM's descriptions for variables without context (cache). |

Contexts are collected in four steps; each step only adds sentences or formulas not found yet, and
every context records its `source`:

1. **LLM synonyms** (`source: "llm"`) are searched in the sentences and in the display formulas.
   Symbolic synonyms are matched case-sensitively after normalizing LaTeX (except a leading `i`/`I`),
   so `I_{Na}`, `I_Na`, `INa` and `i_Na` match, and `I_{Ca, L}` matches `I_CaL`. Textual synonyms are
   matched case-insensitively as whole phrases, in sentences only.
2. **Equation matching** (`source: "equation"`, `src/equation_matching.py`). CellML equations (MathML)
   and paper equations (LaTeX) are parsed into the same canonical expression tree (`src/expr_tree.py`,
   `src/latex_to_tree.py`) and aligned node by node. The best-matching paper equation is added as a
   context (`match_type: "structure"`), and the variable pairs met in the alignment give paper symbols
   (e.g. `i_b_Na` → `I_b,Na`) that are searched like step 1. Each CellML equation is also tried with
   one intermediate variable inlined, because papers often merge equations that CellML splits.
3. **Parent current** (`source: "parent"`), only if steps 1–2 found nothing: a split variable such as
   `i_Ca_L_Na_ds` is searched as `i_Ca_L_Na` and `i_Ca_L`.
4. **Inference** (only if still nothing, and `INFER_MISSING_CONTEXT = True` in `config.py`): the LLM
   describes the variable from its name, unit and formula. This is stored as `inferred_description`
   with a `confidence`, **not** as a context, because it is not evidence from the paper.

Each context has `location` (`"sentence"` or `"formula"`), `sentence` (the sentence, or the formula's
LaTeX with its equation number), `page` and the `keyword` that matched. Formula contexts also have
`formula_id`, `label`, `nearby_sentences` and `defines` (true when the formula defines the variable).
Within a variable, sentences come first, then formulas that define it, then formulas that only use it.

### Stage 3 outputs

| File | Content |
|------|---------|
| `stage3_annotations.json` | One entry per variable (see below). |
| `<provider>_process.json` | The LLM's answer per defining variable (`<formula_component>/<variable>`). Also used as a cache: delete it to ask the LLM again. |

The LLM is called once per variable where it is defined; copies of the variable connected into other
components (e.g. `i_Na` in `intracellular_sodium_concentration`) reuse that answer. The prompt gets the
variable's name, unit, defining component, CellML formula, the CellML equations that use it (so a
maximal current such as `I_CaK` in `i_CaK = d * f * f_Ca * I_CaK` is recognised), textual synonyms and up
to 8 of its Stage 2 contexts (formulas that define it first), or its `inferred_description` when there
are none. A current whose CellML formula is a plain number is always a `parameter`, unless it is a stimulus. The answer
format is enforced with a JSON schema (`PROCESS_SCHEMA` in `src/llm_synonym.py`).

Each entry has:

- `variable_role`: `transport_current`, `parameter` (e.g. `i_NaK_max`, `k_NaCa`), `stimulus_current` or `total_current`
- `mediator` and `mediator_keywords`: the channel, pump or exchanger (or `diffusion`, `none`, `unknown`)
- `participants`: one entry per chemical that moves, with `chemical`, `source`, `sink` and a `*_keywords`
  list for each, so an exchanger such as `i_NaCa` has sodium (extracellular space → cytosol) and calcium
  (cytosol → extracellular space). Stoichiometry is not recorded.
- `confidence` (`high`, `medium`, `low`), `rationale` (a short justification), and `evidence` (the contexts the LLM relied on)

The keywords are search terms for Stage 4; Stage 3 never assigns ontology IDs.

### Stage 4 outputs

| File | Content |
|------|---------|
| `stage4_annotations.json` | The Stage 3 annotation with an ontology term for the mediator and for every participant's chemical, source and sink. |
| `output/ols_cache.json` | OLS search results, shared by all models. Also used as a cache: delete it to search again. |

Each name is searched in the ontologies set by `ONTOLOGIES` in `config.py`: chemicals in ChEBI,
compartments in FMA then GO (FMA has no dyadic space or junctional/network sarcoplasmic reticulum),
mediators in GO. The name is tried first, then its Stage 3 keywords in order, each in every ontology,
so a specific keyword in GO beats a broader one in FMA. Mediators are tried as protein complexes
first (`voltage-gated sodium channel complex`) before `... activity` terms. FMA's "Portion of ..."
naming is tried too (`Portion of cytosol`).

Only exact matches are accepted: the name or keyword must equal the term's label or a synonym
(ignoring case). Each lookup has:

- `term`: `id`, `label`, `ontology`, `iri`, or `null` when nothing matched
- `match`: `"name"` when the name itself was found, `"keyword"` when only one of its keywords was.
  Check these: a keyword can be broader than the name (e.g. dyadic space → *Portion of cytosol*)
- `matched_keyword`, `matched_on` (`label` or `synonym`)
- `candidates`: when nothing matched, the top OLS search results for manual review

A name gets one term per model: the keywords of all its uses in that model are merged first.

---

## Requirements

1. **Python 3.11+**
2. A **LlamaCloud API key** for LlamaParse (https://cloud.llamaindex.ai)
3. **One LLM for Stage 2**, chosen with `LLM_PROVIDER` in `config.py`:

   | `LLM_PROVIDER` | LLM | Cost | Needs |
   |---|---|---|---|
   | `"claude"` (default) | Claude (`CLAUDE_MODEL`) | Paid API | Claude API key (https://platform.claude.com) |
   | `"openai"` | ChatGPT (`OPENAI_MODEL`) | Paid API | OpenAI API key (https://platform.openai.com) |
   | `"ollama"` | Free local model (`OLLAMA_MODEL`, default `llama3.3:70b`) | Free | Ollama installed and the model downloaded |

   The same LLM is used for Stage 3.

```bash
pip install -r requirements.txt
```

### Installing Ollama (for free local models)

Ollama runs open models such as Llama on your own computer, without an API key.

1. Install the Ollama app:
   - **macOS:** download it from https://ollama.com/download, or `brew install ollama`
   - **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`
   - **Windows:** download the installer from https://ollama.com/download
2. Start it: open the Ollama app, or run `ollama serve` in a terminal.
3. Download the model(s):
   ```bash
   ollama pull llama3.3:70b   # Stage 2 (OLLAMA_MODEL)
   ```
4. Check that it works: `ollama list` should show the models.

`llama3.3:70b` is a ~43 GB download and needs about 48 GB of RAM (or GPU memory) to run.
On smaller machines set `OLLAMA_MODEL` to a smaller model, for example `llama3.1:8b` or `qwen2.5:7b`;
synonyms from small models are noticeably shorter and less complete. If Ollama runs on another
computer, set `OLLAMA_HOST` (default `http://localhost:11434`).

## Setup

1. Put each model's CellML file and paper PDF in `data/`, named as listed in `MODELS` in `config.py`
   (e.g. `data/hodgkin_huxley.cellml` and `data/hodgkin_huxley.pdf`).
2. Set your LlamaParse API key, either as an environment variable:
   ```bash
   export LLAMA_CLOUD_API_KEY="llx-..."
   ```
   or by pasting it into `LLAMA_CLOUD_API_KEY` in `config.py` (do not commit the key).
3. Choose the LLM in `config.py` (`LLM_PROVIDER = "claude"`, `"openai"` or `"ollama"`) and, for the
   paid APIs, set its key the same way (environment variable or `config.py`):
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."   # claude
   export OPENAI_API_KEY="sk-..."          # openai
   ```
   `"ollama"` needs no key, only the installation steps above.

## How to Run

```bash
# All stages for all models in config.py
python main.py

# All stages for one model
python main.py --model hodgkin_huxley

# Only one stage
python main.py --model hodgkin_huxley --stage 1
python main.py --model hodgkin_huxley --stage 2
python main.py --model hodgkin_huxley --stage 3
python main.py --model hodgkin_huxley --stage 4

# CellML variables only (no LlamaParse or LLM call)
python main.py --stage 1 --skip-pdf
```

---

## Project Folder Structure

```
semantic_annotation/
├── main.py                  ← CLI entry point
├── config.py                ← Models list, API keys, LLM provider/model, paths
├── requirements.txt
├── data/                    ← CellML + PDF files
├── output/                  ← JSON output per model and stage
└── src/
    ├── pipeline.py          ← Stage orchestrator
    ├── cellml_reader.py     ← Stage 1: CellML variables, units, formulas (libcellml)
    ├── rdf_unit_cellml.ttl  ← Stage 1: baseline unit → OPB term mapping
    ├── baseline_units.cellml← Stage 1: baseline unit definitions
    ├── mathml_to_text.py    ← Stage 1: MathML → readable formula (and expression tree)
    ├── pdf_reader.py        ← Stage 1: LlamaParse text + formula extraction
    ├── sentence_splitter.py ← Sentence splitting
    ├── llm_synonym.py       ← Stage 2-3: LLM calls and answer schemas (Claude / OpenAI / Ollama)
    ├── prompt.py            ← Stage 2-3: synonym, inference and process prompts
    ├── context_search.py    ← Stage 2: evidence sentence + formula search
    ├── expr_tree.py         ← Stage 2: canonical expression trees
    ├── latex_to_tree.py     ← Stage 2: paper LaTeX → expression tree
    ├── equation_matching.py ← Stage 2: CellML ↔ paper equation matching
    └── ols_lookup.py        ← Stage 4: ontology terms through OLS (ChEBI, FMA, GO)
```
