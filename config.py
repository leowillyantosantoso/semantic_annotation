"""
Project Configuration
"""

import os

# LLM used in Stage 2 (synonyms) and Stage 3 (biological process inference):
#   "claude" - Anthropic Claude API (paid, needs ANTHROPIC_API_KEY)
#   "openai" - OpenAI ChatGPT API   (paid, needs OPENAI_API_KEY)
#   "ollama" - free local model through Ollama (no API key; see README)
# Each provider keeps its own caches: output/<model>/<provider>_synonyms.json, <provider>_process.json
LLM_PROVIDER = "claude"

# Claude (Anthropic) settings.
# Paste your Claude API key between the quotes below (remove it before committing),
# or leave it empty to use the ANTHROPIC_API_KEY environment variable instead.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "sk-ant-")
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_EFFORT = "medium"  # low | medium | high | xhigh | max

# Stage 2: for variables with no sentence or equation in the paper, ask the LLM to describe
# the variable from its formula. Saved as "inferred_description" (not as a context), cached in
# output/<model>/<provider>_inferred.json. Set to False to skip these extra LLM calls.
INFER_MISSING_CONTEXT = True

# OpenAI (ChatGPT) settings.
# Paste your OpenAI API key between the quotes below (remove it before committing),
# or leave it empty to use the OPENAI_API_KEY environment variable instead.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-5"  # any model that supports structured outputs, e.g. "gpt-5-mini"

# Ollama (free, local) settings. Download the model first:  ollama pull llama3.3:70b
# llama3.3:70b needs ~43 GB of memory; on smaller machines use e.g. "llama3.1:8b" or "qwen2.5:7b".
OLLAMA_MODEL = "llama3.3:70b"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# LlamaParse (LlamaCloud) API key for Stage 1 paper extraction.
# Prefer setting the environment variable:  export LLAMA_CLOUD_API_KEY="llx-..."
# or paste your key between the quotes below (do not commit it to git).
LLAMA_CLOUD_API_KEY = os.getenv("LLAMA_CLOUD_API_KEY", "llx-")

# LlamaParse tier: "fast" (no AI, no formulas), "cost_effective", "agentic", "agentic_plus".
# "agentic" or higher is recommended so equations come back as LaTeX.
LLAMAPARSE_TIER = "agentic"

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# Unit selection: a CellML variable is extracted when its units are compatible (libcellml)
# with any baseline unit that rdf_unit_cellml.ttl maps to TARGET_OPB.
UNITS_TTL = os.path.join(BASE_DIR, "src", "rdf_unit_cellml.ttl")
BASELINE_UNITS = os.path.join(BASE_DIR, "src", "baseline_units.cellml")
TARGET_OPB = "OPB_00318"  # Charge flow rate: C_per_m3_s, C_per_m2_s, C_per_m_s, fA

# Stage 4: ontologies searched through OLS (https://www.ebi.ac.uk/ols4), in order of preference
ONTOLOGIES = {
    "chemical": ["chebi"],
    "compartment": ["fma", "go"],  # FMA has no dyadic space or junctional/network SR; GO is the fallback
    "mediator": ["go"],
}
# OLS answers, shared by all models (delete to search again)
OLS_CACHE = os.path.join(OUTPUT_DIR, "ols_cache.json")

# List of models to process
# Each item: (cellml file, pdf file, paper_title)
MODELS = [
    {
        "cellml": "hodgkin_huxley.cellml",
        "pdf": "hodgkin_huxley.pdf",
        "paper_title": "A quantitative description of membrane current and its application to conduction and excitation in nerve"
    },
    {
        "cellml": "jafri_rice_winslow_1998.cellml",
        "pdf": "jafri_rice_winslow_1998.pdf",
        "paper_title": "Cardiac Ca2+ Dynamics: The Roles of Ryanodine Receptor Adaptation and Sarcoplasmic Reticulum Load"
    },
    {
        "cellml": "noble1962.cellml",
        "pdf": "noble1962.pdf",
        "paper_title": "A modification of the Hodgkin-Huxley equations applicable to Purkinje fibre action and pace-maker potentials"
    },
    {
        "cellml": "luo_rudy_1994.cellml",
        "pdf": "luo_rudy_1994.pdf",
        "paper_title": "A dynamic model of the cardiac ventricular action potential"
    },
    {
        "cellml": "luo_rudy_1991.cellml",
        "pdf": "luo_rudy_1991.pdf",
        "paper_title": "A model of the ventricular cardiac action potential"
    },
    {
        "cellml": "difrancesco_noble.cellml",
        "pdf": "difrancesco_noble.pdf",
        "paper_title": "A model of cardiac electrical activity incorporating ionic pumps and concentration changes"
    },
    {
        "cellml": "noble_varghese_kohl_noble_1998.cellml",
        "pdf": "noble_varghese_kohl_noble_1998.pdf",
        "paper_title": "Improved guinea-pig ventricular cell model incorporating a diadic space, IKr and IKs, and length- and tension-dependent processes"
    },
    {
        "cellml": "tentusscher_noble_noble_panfilov_2004.cellml",
        "pdf": "tentusscher_noble_noble_panfilov_2004.pdf",
        "paper_title": "A model for human ventricular tissue"
    },
]
