import os
import sys
import json
from src.database import init_db, get_session
from src.models import PipelineTask, OntologyTerm

CHEBI_OBO = r"C:\Imam\projects\cellml_reader\ontology\chebi.obo"
GO_OBO = r"C:\Imam\projects\cellml_reader\ontology\go.obo"

INITIAL_TASKS = [
    {
        "cellml_file": "hodgkin_huxley.cellml",
        "pdf_file": "hodgkin_huxley.pdf",
        "paper_title": "A quantitative description of membrane current and its application to conduction and excitation in nerve",
        "unit_filters": ["microA_per_cm2"]
    },
    {
        "cellml_file": "jafri_rice_winslow_1998.cellml",
        "pdf_file": "jafri_rice_winslow_1998.pdf",
        "paper_title": "Cardiac Ca2+ Dynamics: The Roles of Ryanodine Receptor Adaptation and Sarcoplasmic Reticulum Load",
        "unit_filters": ["pA"]
    },
    {
        "cellml_file": "noble1962.cellml",
        "pdf_file": "noble1962.pdf",
        "paper_title": "A modification of the Hodgkin-Huxley equations applicable to Purkinje fibre action and pace-maker potentials",
        "unit_filters": ["microA_per_cm2", "microA"]
    },
    {
        "cellml_file": "luo_rudy_1994.cellml",
        "pdf_file": "luo_rudy_1994.pdf",
        "paper_title": "A dynamic model of the cardiac ventricular action potential",
        "unit_filters": ["uA_per_uF"]
    },
    {
        "cellml_file": "luo_rudy_1991.cellml",
        "pdf_file": "luo_rudy_1991.pdf",
        "paper_title": "A model of the ventricular cardiac action potential",
        "unit_filters": ["uA_per_uF"]
    },
    {
        "cellml_file": "difrancesco_noble.cellml",
        "pdf_file": "difrancesco_noble.pdf",
        "paper_title": "A model of cardiac electrical activity incorporating ionic pumps and concentration changes",
        "unit_filters": ["uA_per_uF", "nA"]
    }
]

def parse_and_seed_obo(obo_path, ontology_type, session):
    """Parse OBO file and bulk insert terms into the database."""
    if not os.path.exists(obo_path):
        print(f"WARNING: OBO file not found at {obo_path}. Skipping.")
        return

    print(f"Parsing {ontology_type.upper()} OBO file: {obo_path}...")
    
    current_id = None
    current_name = None
    current_synonyms = []
    
    terms_to_insert = []
    batch_size = 5000
    count = 0

    with open(obo_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            
            if line == "[Term]":
                # Save previous term
                if current_id and current_name:
                    terms_to_insert.append({
                        "id": current_id,
                        "name": current_name,
                        "synonyms": ",".join(current_synonyms),
                        "ontology_type": ontology_type
                    })
                    count += 1
                    
                    if len(terms_to_insert) >= batch_size:
                        session.bulk_insert_mappings(OntologyTerm, terms_to_insert)
                        session.commit()
                        terms_to_insert = []
                        print(f"  Inserted {count} terms...")
                        
                current_id = None
                current_name = None
                current_synonyms = []
                
            elif line.startswith("id: "):
                current_id = line[4:].strip()
            elif line.startswith("name: "):
                current_name = line[6:].strip()
            elif line.startswith("synonym: "):
                # Extract synonym text inside quotes
                start = line.find('"')
                end = line.find('"', start + 1)
                if start != -1 and end != -1:
                    current_synonyms.append(line[start + 1:end].strip())

        # Insert last term
        if current_id and current_name:
            terms_to_insert.append({
                "id": current_id,
                "name": current_name,
                "synonyms": ",".join(current_synonyms),
                "ontology_type": ontology_type
            })
            count += 1

    if terms_to_insert:
        session.bulk_insert_mappings(OntologyTerm, terms_to_insert)
        session.commit()
        
    print(f"SUCCESS: Seeded {count} terms from {ontology_type.upper()} into database.")

def main():
    print("Initializing database tables...")
    try:
        init_db()
        print("SUCCESS: Tables created.")
    except Exception as e:
        print("ERROR creating tables:", e)
        print("Please verify that you have manually created the 'cellml_reader' database.")
        sys.exit(1)

    with get_session() as session:
        # 1. Seed Initial Pipeline Tasks
        task_count = session.query(PipelineTask).count()
        if task_count == 0:
            print("Seeding initial pipeline tasks...")
            for task_data in INITIAL_TASKS:
                task = PipelineTask(
                    cellml_file=task_data["cellml_file"],
                    pdf_file=task_data["pdf_file"],
                    paper_title=task_data["paper_title"],
                    unit_filters=task_data["unit_filters"],
                    status="pending"
                )
                session.add(task)
            session.commit()
            print("SUCCESS: Seeded initial pipeline tasks.")
        else:
            print(f"Pipeline tasks already seeded ({task_count} found). Skipping.")

        # 2. Seed Ontologies
        ontology_count = session.query(OntologyTerm).count()
        if ontology_count == 0:
            parse_and_seed_obo(CHEBI_OBO, "chebi", session)
            parse_and_seed_obo(GO_OBO, "go", session)
        else:
            print(f"Ontology terms already seeded ({ontology_count} found). Skipping.")

if __name__ == "__main__":
    main()
