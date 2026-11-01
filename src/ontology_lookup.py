"""
Modul untuk mencari ID ontologi dari file .obo lokal.
Membaca file CHEBI dan GO, lalu cari berdasarkan keyword.
"""

import os


def load_obo_file(obo_path):
    """Baca file .obo dan return dict: {nama_lowercase: id}

    Contoh hasil:
    {
        "potassium(1+)": "CHEBI:29103",
        "sodium(1+)": "CHEBI:29101",
        "potassium channel activity": "GO:0005267",
    }
    """
    terms = {}

    if not os.path.isfile(obo_path):
        print(f"WARNING: File ontologi tidak ditemukan: {obo_path}")
        return terms

    current_id = None
    current_names = []

    with open(obo_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()

            if line == "[Term]":
                # Simpan term sebelumnya
                if current_id and current_names:
                    for name in current_names:
                        terms[name.lower()] = current_id
                current_id = None
                current_names = []

            elif line.startswith("id: "):
                current_id = line[4:]

            elif line.startswith("name: "):
                current_names.append(line[6:])

            elif line.startswith("synonym: "):
                # Format: synonym: "some name" EXACT []
                # Ambil text antara tanda kutip pertama
                start = line.find('"')
                end = line.find('"', start + 1)
                if start != -1 and end != -1:
                    synonym_text = line[start + 1:end]
                    current_names.append(synonym_text)

    # Jangan lupa term terakhir
    if current_id and current_names:
        for name in current_names:
            terms[name.lower()] = current_id

    return terms


def search_ontology(terms_dict, keyword):
    """Cari ID ontologi berdasarkan keyword.

    Args:
        terms_dict: Dict hasil dari load_obo_file()
        keyword: String keyword yang dicari (misalnya "sodium")

    Returns:
        String ID ontologi (misalnya "CHEBI:29101") atau None jika tidak ketemu
    """
    keyword_lower = keyword.lower().strip()

    # Coba exact match dulu
    if keyword_lower in terms_dict:
        return terms_dict[keyword_lower]

    # Coba cari yang mengandung keyword
    for name, ont_id in terms_dict.items():
        if keyword_lower in name:
            return ont_id

    return None
