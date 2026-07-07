"""
Project Configuration
"""

import os

# Model LLM
MODEL_NAME = "qwen2.5:7b"

# Path dasar
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# Path ontologi
CHEBI_OBO = os.path.join(BASE_DIR, "ontology", "chebi.obo")
GO_OBO = os.path.join(BASE_DIR, "ontology", "go.obo")

# Daftar model yang akan diproses
# Setiap item: (file_cellml, file_pdf, paper_title)
MODELS = [
    # {
    #     "cellml": "hodgkin_huxley.cellml",
    #     "pdf": "hodgkin_huxley.pdf",
    #     "paper_title": "A quantitative description of membrane current and its application to conduction and excitation in nerve"
    # },
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
    # {
    #     "cellml": "luo_rudy_1991.cellml",
    #     "pdf": "luo_rudy_1991.pdf",
    #     "paper_title": "A model of the ventricular cardiac action potential"
    # },
    {
        "cellml": "difrancesco_noble.cellml",
        "pdf": "difrancesco_noble.pdf",
        "paper_title": "A model of cardiac electrical activity incorporating ionic pumps and concentration changes"
    },
]
