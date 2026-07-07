# CellML Reader Pipeline & Dashboard

Pipeline otomatis untuk membaca variabel dari model CellML, mencari kalimat pendukung di paper ilmiah PDF, dan menghasilkan anotasi ontologi terstruktur (ChEBI, GO, FMA) dengan antarmuka Web Dashboard interaktif.

Sistem ini didukung oleh basis data **PostgreSQL** untuk menyimpan status progres secara transaksional per variabel (*state machine*), pencarian teks cepat (*GIN Indexing*), serta sistem caching respons LLM (*Semantic Caching*) untuk menghindari kueri LLM berulang.

---

## Fitur Utama

1. **Web UI Dashboard:** Antarmuka modern (Dark/Light mode) untuk memantau status model, melihat kemajuan progres variabel secara real-time, dan menginspeksi hasil anotasi.
2. **Reprocess Granular:** Kemampuan untuk memproses ulang (*reprocessing*) seluruh model (Reset Total) atau hanya kueri anotasi LLM (Process 3) langsung dari dashboard. Anda juga dapat memicu proses ulang khusus untuk satu variabel tertentu yang gagal (*failed*).
3. **Database-Driven Caching:** Caching otomatis kueri sinonim dan kueri anotasi dari LLM (Ollama) sehingga eksekusi berikutnya berjalan instan.
4. **Bypass PDF parsing:** Dokumen PDF yang sudah dibaca dan dipotong per kalimat disimpan di database. Kueri berikutnya langsung mencari di database tanpa mem-parsing PDF fisik lagi.

---

## Prasyarat System

1. **Python 3.12+**
2. **PostgreSQL** berjalan lokal di port `5432` dengan akun pengguna `ml_user` (password: `ml_user`).
3. **Ollama** dengan model `qwen2.5:7b` terpasang.
4. **Tesseract OCR** (untuk ekstraksi teks PDF hasil pemindaian/scan).

---

## Instalasi & Setup Database

### 1. Persiapan Kode & Dependencies
```bash
# Clone & masuk ke direktori project
cd cellml_reader

# Aktifkan virtual environment (.venv)
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# Install dependencies tambahan (FastAPI, SQLAlchemy, Uvicorn, Psycopg2)
pip install fastapi uvicorn sqlalchemy psycopg2-binary
```

### 2. Setup Database PostgreSQL
Anda memiliki dua opsi untuk setup database:

#### Opsi A: Restore dari SQL Dump (Direkomendasikan - Instan & Cepat)
Gunakan file dump `db_backup.sql` yang sudah disertakan untuk mengembalikan seluruh tabel, data model, dan daftar ontologi (~250.000 term ChEBI + GO) secara instan:
1. Buat database baru bernama `cellml_reader` di pgAdmin / pgSql CLI.
2. Jalankan perintah restore di terminal Anda (masukkan password `ml_user` jika diminta):
   ```bash
   psql -U ml_user -h 127.0.0.1 -d cellml_reader -f db_backup.sql
   ```

#### Opsi B: Inisialisasi & Seeding Manual
Jika ingin membuat tabel baru dan mem-parsing ulang file `.obo` mentah:
1. Buat database kosong bernama `cellml_reader` secara manual.
2. Pastikan file `ontology/chebi.obo` dan `ontology/go.obo` tersedia.
3. Jalankan script inisialisasi:
   ```bash
   python init_db.py
   ```
   *(Proses parsing OBO file mentah ke database memakan waktu sekitar 15-30 detik).*

---

## Cara Menjalankan Program

### 1. Menjalankan Dashboard Web UI (Interaktif)
Jalankan server web FastAPI menggunakan Uvicorn:
```bash
python -m uvicorn web_server:app --port 8000 --reload
```
Buka browser Anda dan akses:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

*Di sini Anda dapat mengklik **Run Task** untuk memicu pipeline, memantau kemajuan progres, dan mengklik variabel untuk menginspeksi sinonim, kalimat bukti, serta file JSON anotasi final.*

### 2. Menjalankan via CLI Terminal (Non-Interaktif)
Jika Anda ingin menjalankan pipeline untuk semua tugas berstatus `pending`/`failed` di database secara langsung lewat terminal:
```bash
python main.py
```

---

## Struktur Folder Proyek

```
cellml_reader/
├── main.py              ← CLI Entry point (memproses task di DB)
├── web_server.py        ← Backend Server FastAPI (REST Controller)
├── init_db.py           ← Pembuat tabel & seeder awal database
├── db_backup.sql        ← Backup database SQL (ChEBI, GO, & schema)
├── config.py            ← Konfigurasi model LLM & path file
├── data/                ← File CellML + PDF (Hodgkin, Jafri, Noble, Luo, DiFrancesco)
├── ontology/            ← File mentah .obo (CHEBI, GO)
├── output/              ← Hasil keluaran file JSON (untuk backup)
├── static/              ← Frontend dashboard SPA (HTML, CSS, JS)
└── src/                 ← Source code modul internal
    ├── models.py        ← SQLAlchemy Declarative Entities (JPA Style)
    ├── database.py      ← Session manager, caching, & housekeeping
    ├── pipeline.py      ← Orchestrator pipeline per tahapan (Stage 1, 2, 3)
    ├── cellml_reader.py
    ├── llm_synonym.py
    ├── pdf_reader.py
    ├── sentence_splitter.py
    ├── context_search.py
    ├── process3.py
    └── process3_prompt.py
```
