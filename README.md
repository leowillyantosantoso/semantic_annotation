CellML Reader Pipeline

Pipeline otomatis untuk membaca variabel dari model CellML, mencari konteks di paper ilmiah, dan menghasilkan anotasi ontologi terstruktur.

Prasyarat

Python 3.12+
Ollama dengan model qwen2.5:7b (atau llama3.3)
Tesseract OCR (untuk ekstraksi PDF)
Instalasi

# 1. Clone repository
git clone https://github.com/ibgithub/cellml_reader.git
cd cellml_reader

# 2. Buat virtual environment
python -m venv .venv

# 3. Aktifkan venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 4. Install dependencies
pip install libcellml ollama pdfplumber "unstructured[pdf]"

# 5. Install Tesseract OCR
# Windows: Download dari https://github.com/UB-Mannheim/tesseract/wiki
# Linux: sudo apt install tesseract-ocr
# Mac: brew install tesseract

# 6. Download file ontologi dan taruh di folder ontology/
# CHEBI: https://ftp.ebi.ac.uk/pub/databases/chebi/ontology/chebi.obo
# GO: https://purl.obolibrary.org/obo/go.obo
mkdir ontology
# Download chebi.obo dan go.obo ke folder ontology/

# 7. Pastikan Ollama running dengan model
ollama pull qwen2.5:7b
ollama serve
Struktur Folder

cellml_reader/
├── main.py              ← Entry point
├── config.py            ← Konfigurasi model & path
├── data/                ← File CellML + PDF
├── ontology/            ← File .obo (CHEBI, GO)
├── output/              ← Hasil output JSON
└── src/                 ← Source code modules
    ├── cellml_reader.py
    ├── llm_synonym.py
    ├── pdf_reader.py
    ├── sentence_splitter.py
    ├── context_search.py
    ├── ontology_lookup.py
    ├── process3.py
    └── process3_prompt.py
Cara Menjalankan

python main.py
Program akan memproses model yang terdaftar di config.py dan menghasilkan:

output/<nama_model>_output.json — Variabel + kalimat relevan dari paper
output/<nama_model>_annotations.json — Anotasi ontologi (CHEBI, GO)
Konfigurasi

Edit config.py untuk:

Mengganti model LLM (MODEL_NAME)
Menambah/mengurangi model yang diproses (MODELS)
Catatan

Loading ontologi CHEBI membutuhkan ~30-60 detik di awal (file 230MB)
Setiap model membutuhkan beberapa menit tergantung jumlah variabel
Filter unit yang didukung: microA_per_cm2, uA_per_mm2, uA_per_mmsq, nanoA
