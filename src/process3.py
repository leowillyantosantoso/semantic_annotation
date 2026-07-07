"""
Proses 3: Inferensi proses biologi dan lookup ontologi (format baru).

Input: hasil dari Proses 2 (variabel + kalimat yang ditemukan)
Output: anotasi terstruktur dengan ID ontologi dari CHEBI, GO, FMA
"""

import json
import ollama

from src.process3_prompt import PROCESS3_PROMPT_TEMPLATE
from src.ontology_lookup import search_ontology
from src.llm_synonym import _extract_json


# Hardcode FMA lookup (file FMA sangat besar, hanya butuh beberapa term)
FMA_LOOKUP = {
    "intracellular": "FMA:70015",
    "intracellular space": "FMA:70015",
    "intracellular compartment": "FMA:70015",
    "cytoplasm": "FMA:66835",
    "cytosol": "FMA:66835",
    "extracellular": "FMA:70022",
    "extracellular space": "FMA:70022",
    "extracellular compartment": "FMA:70022",
    "sarcoplasmic reticulum": "FMA:67905",
}


def run_process3(process2_results, config):
    """Jalankan Proses 3 untuk semua variabel.

    Args:
        process2_results: List dari dict hasil Proses 2
        config: Dict konfigurasi (model_name, chebi_dict, go_dict)

    Returns:
        List of dict anotasi terstruktur (format baru)
    """
    annotations = []

    for i, var_result in enumerate(process2_results):

        variable_name = var_result["variable"]
        component = var_result["component"]
        unit = var_result["unit"]
        contexts = var_result["contexts"]

        print(f"  [Proses 3] Variabel {i+1}: {variable_name}")

        # Ambil kalimat-kalimat sebagai evidence
        evidence_sentences = []
        for ctx in contexts[:5]:  # Maksimal 5 kalimat
            evidence_sentences.append(ctx["sentence"])

        # Kalau tidak ada evidence, skip
        if not evidence_sentences:
            print(f"    Tidak ada kalimat evidence, skip.")
            continue

        # Buat prompt
        evidence_text = "\n".join(f"- {s}" for s in evidence_sentences)
        component_title = component.replace('_', ' ').title()

        prompt = PROCESS3_PROMPT_TEMPLATE.format(
            variable_name=variable_name,
            unit=unit,
            component=component,
            component_title=component_title,
            evidence_sentences=evidence_text
        )

        from src.database import check_llm_cache, save_llm_cache

        # Kirim ke LLM
        try:
            cached_content = check_llm_cache(prompt)
            if cached_content:
                content = cached_content
                print("    [LLM Cache Hit] Menggunakan response anotasi dari cache.")
            else:
                response = ollama.chat(
                    model=config["model_name"],
                    messages=[{"role": "user", "content": prompt}]
                )
                content = response["message"]["content"]
                save_llm_cache(prompt, content)
        except Exception as e:
            print(f"    ERROR LLM: {e}")
            continue

        # Parse JSON dari response LLM
        try:
            json_text = _extract_json(content)
            llm_result = json.loads(json_text)
        except Exception as e:
            print(f"    ERROR parse JSON: {e}")
            continue

        # Lookup ontologi berdasarkan keywords dari LLM
        annotation = enrich_with_ontology(llm_result, config)
        annotations.append(annotation)

        print(f"    OK: {annotation.get('name', variable_name)}")

    return annotations


def enrich_with_ontology(llm_result, config):
    """Tambahkan ID ontologi ke hasil LLM berdasarkan keywords.

    Args:
        llm_result: Dict hasil dari LLM (format baru)
        config: Dict yang berisi chebi_dict dan go_dict

    Returns:
        Dict yang sudah dilengkapi ID ontologi
    """
    chebi_dict = config["chebi_dict"]
    go_dict = config["go_dict"]

    # Lookup mediator dari GO
    mediator_keywords = llm_result.get("mediator_ontology_keywords", [])
    mediator_id = _lookup_first_match(go_dict, mediator_keywords)
    llm_result["mediator_ontology_id"] = mediator_id

    # Lookup participants
    participants = llm_result.get("participants", [])
    for participant in participants:

        # Lookup ion dari CHEBI
        ion_keywords = participant.get("ion_ontology_keywords", [])
        participant["ion_ontology_id"] = _lookup_first_match(chebi_dict, ion_keywords)

        # Lookup source dari FMA (hardcoded)
        source_keywords = participant.get("source_ontology_keywords", [])
        participant["source_ontology_id"] = _lookup_fma(source_keywords)

        # Lookup sink dari FMA (hardcoded)
        sink_keywords = participant.get("sink_ontology_keywords", [])
        participant["sink_ontology_id"] = _lookup_fma(sink_keywords)

    return llm_result


def _lookup_first_match(terms_dict, keywords):
    """Cari ID ontologi dari daftar keywords, return yang pertama ketemu."""
    for keyword in keywords:
        result = search_ontology(terms_dict, keyword)
        if result:
            return result
    return "NOT_FOUND"


def _lookup_fma(keywords):
    """Cari ID FMA dari hardcoded lookup."""
    for keyword in keywords:
        keyword_lower = keyword.lower().strip()
        if keyword_lower in FMA_LOOKUP:
            return FMA_LOOKUP[keyword_lower]
        # Coba partial match
        for fma_key, fma_id in FMA_LOOKUP.items():
            if keyword_lower in fma_key or fma_key in keyword_lower:
                return fma_id
    return "NOT_FOUND"


def db_lookup_first_match(session, keywords, ontology_type):
    """Cari ID ontologi di database berdasarkan keywords. Exact match diutamakan, baru substring match."""
    from src.models import OntologyTerm
    from sqlalchemy import func
    if not keywords:
        return "NOT_FOUND"
        
    # 1. Exact match
    for kw in keywords:
        kw_clean = kw.strip().lower()
        if not kw_clean:
            continue
        term = session.query(OntologyTerm).filter(
            OntologyTerm.ontology_type == ontology_type,
            (func.lower(OntologyTerm.name) == kw_clean)
        ).first()
        if term:
            return term.id
            
    # 2. Substring match (jika exact match tidak ketemu)
    for kw in keywords:
        kw_clean = kw.strip().lower()
        if len(kw_clean) < 3: # Hindari pencarian substring yang terlalu pendek
            continue
        term = session.query(OntologyTerm).filter(
            OntologyTerm.ontology_type == ontology_type,
            (func.lower(OntologyTerm.name).like(f"%{kw_clean}%")) | (OntologyTerm.synonyms.ilike(f"%{kw_clean}%"))
        ).first()
        if term:
            return term.id
            
    return "NOT_FOUND"


def enrich_with_ontology_db(session, llm_result):
    """Tambahkan ID ontologi ke hasil LLM menggunakan database query.
    
    Args:
        session: SQLAlchemy session
        llm_result: Dict hasil dari LLM
        
    Returns:
        Dict yang sudah dilengkapi ID ontologi
    """
    # Lookup mediator dari GO
    mediator_keywords = llm_result.get("mediator_ontology_keywords", [])
    mediator_id = db_lookup_first_match(session, mediator_keywords, "go")
    llm_result["mediator_ontology_id"] = mediator_id

    # Lookup participants
    participants = llm_result.get("participants", [])
    for participant in participants:
        # Lookup ion dari CHEBI
        ion_keywords = participant.get("ion_ontology_keywords", [])
        participant["ion_ontology_id"] = db_lookup_first_match(session, ion_keywords, "chebi")

        # Lookup source dari FMA (hardcoded)
        source_keywords = participant.get("source_ontology_keywords", [])
        participant["source_ontology_id"] = _lookup_fma(source_keywords)

        # Lookup sink dari FMA (hardcoded)
        sink_keywords = participant.get("sink_ontology_keywords", [])
        participant["sink_ontology_id"] = _lookup_fma(sink_keywords)

    return llm_result
