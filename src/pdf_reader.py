"""
Modul untuk ekstraksi teks dari PDF.
Primary: unstructured (dengan Tesseract OCR)
Fallback: pdfplumber
"""

import os

# Tambahkan Tesseract ke PATH jika belum ada
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR"
if TESSERACT_PATH not in os.environ.get("PATH", ""):
    os.environ["PATH"] = TESSERACT_PATH + ";" + os.environ.get("PATH", "")


def extract_pdf_text(pdf_path):
    """Ekstrak teks dari PDF.

    Coba pakai unstructured dulu (strategy='auto' dengan Tesseract).
    Kalau gagal, fallback ke pdfplumber.
    """

    # Coba unstructured
    try:
        text = _extract_with_unstructured(pdf_path)
        if text.strip():
            return text
    except Exception as e:
        print(f"    unstructured gagal: {e}")
        print(f"    Fallback ke pdfplumber...")

    # Fallback: pdfplumber
    return _extract_with_pdfplumber(pdf_path)


def _extract_with_unstructured(pdf_path):
    """Ekstrak teks menggunakan unstructured (strategy='fast')."""
    from unstructured.partition.pdf import partition_pdf

    elements = partition_pdf(
        filename=pdf_path,
        strategy="fast",
        languages=["eng"]
    )

    text = "\n".join(str(el) for el in elements if hasattr(el, "text"))
    return text


def _extract_with_pdfplumber(pdf_path):
    """Ekstrak teks menggunakan pdfplumber (fallback)."""
    import pdfplumber

    text = ""

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

    return text
