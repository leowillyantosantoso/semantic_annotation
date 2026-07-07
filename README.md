# Semantic Annotation Pipeline & Dashboard

An automated pipeline for reading from CellML models, searching for supporting sentences in PDF scientific papers, and generating structured ontology annotations (ChEBI, GO, FMA, OPB) with an interactive Web Dashboard interface.

The system is powered by a **PostgreSQL** database for transactional progress storage per variable (state machine), fast text search (GIN Indexing), and an LLM response caching system (Semantic Caching) to avoid repeated LLM queries.

---

## Main Feature

1. **Web UI Dashboard:** A modern interface (Dark/Light mode) for monitoring model status, viewing real-time variable progress, and inspecting annotation results.
2. **Reprocess Granular:** The ability to reprocess the entire model (*reprocessing*) or just the LLM annotation query (Process 3) directly from the dashboard. You can also trigger a specific reprocessing for a specific failed variable (*failed*).
3. **Database-Driven Caching:** Automatic caching of synonym and annotation queries from LLM (Ollama) so that subsequent executions are instantaneous.
4. **Bypass PDF parsing:** The read and parsed PDF documents are stored in the database. Subsequent queries directly search the database without parsing the physical PDF.

---

## System Requirements

1. **Python 3.12+**
2. **PostgreSQL** running locally on port 5432 with the user account ml_user (password: ml_user).
3. **Ollama** with the qwen2.5:7b model installed.
4. **Tesseract OCR** (for text extraction of scanned PDFs).

---

## Database Installation & Setup

### 1. Code Preparation & Dependencies
```bash
# Clone & enter the project directory
cd cellml_reader

# Activate the virtual environment (.venv)
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# Install additional dependencies (FastAPI, SQLAlchemy, Uvicorn, Psycopg2)
pip install fastapi uvicorn sqlalchemy psycopg2-binary
```

### 2. PostgreSQL Database Setup
You have two options for database setup:

#### Option A: Restore from SQL Dump (Recommended - Instant & Fast)
Use the included `db_backup.sql` dump file to instantly restore all tables, model data, and ontology lists (~250,000 ChEBI + GO terms):
1. Create a new database named `cellml_reader` in the pgAdmin/pgSql CLI.
2. Run the restore command in your terminal (enter the `ml_user` password if prompted):
   ```bash
   psql -U ml_user -h 127.0.0.1 -d cellml_reader -f db_backup.sql
   ```

#### Option B: Manual Initialization & Seeding
If you want to create new tables and re-parse the raw `.obo` files:
1. Manually create an empty database named `cellml_reader`.
2. Ensure the `ontology/chebi.obo` and `ontology/go.obo` files exist.
3. Run the initialization script:
   ```bash
   python init_db.py
   ```
   *(The process of parsing the raw OBO file into the database takes about 15-30 seconds).*

---

## How to Run the Program

### 1. Running the Web UI Dashboard (Interactive)
Run the FastAPI web server using Uvicorn:
```bash
python -m uvicorn web_server:app --port 8000 --reload
```
Open your browser and access:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

*Here you can click **Run Task** to trigger the pipeline, monitor progress, and click on variables to inspect synonyms, proof sentences, and the final annotation JSON file.*

### 2. Running via CLI Terminal (Non-Interactive)
If you want to run a pipeline for all pending/failed tasks in the database directly from the terminal:
```bash
python main.py
```

---

## Project Folder Structure

```
cellml_reader/
├── main.py              ← CLI Entry point (processing tasks in the DB)
├── web_server.py        ← Backend Server FastAPI (REST Controller)
├── init_db.py           ← Database table creator & initial seeder
├── db_backup.sql        ← SQL database backup (ChEBI, GO, & schema)
├── config.py            ← LLM model configuration & file path
├── data/                ← CellML + PDF files (Hodgkin, Jafri, Noble, Luo, DiFrancesco)
├── ontology/            ← Raw file .obo (CHEBI, GO)
├── output/              ← JSON file output (for backup)
├── static/              ← Frontend dashboard SPA (HTML, CSS, JS)
└── src/                 ← Internal module source code
    ├── models.py        ← SQLAlchemy Declarative Entities (JPA Style)
    ├── database.py      ← Session manager, caching, & housekeeping
    ├── pipeline.py      ← Orchestrator pipeline per stage (Stage 1, 2, 3)
    ├── cellml_reader.py
    ├── llm_synonym.py
    ├── pdf_reader.py
    ├── sentence_splitter.py
    ├── context_search.py
    ├── process3.py
    └── process3_prompt.py
```
