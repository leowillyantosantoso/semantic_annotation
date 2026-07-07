import os
import json
from datetime import datetime
from sqlalchemy.orm import Session
from src.models import PipelineTask, VariableRun, PdfDocument, PdfSentence
from src.database import get_session, check_llm_cache, save_llm_cache
from src.cellml_reader import read_cellml
from src.pdf_reader import extract_pdf_text
from src.sentence_splitter import split_sentences
from src.llm_synonym import generate_synonyms, _extract_json
from src.context_search import search_context
from src.process3_prompt import PROCESS3_PROMPT_TEMPLATE
from src.process3 import enrich_with_ontology_db
import ollama
from config import MODEL_NAME

DATA_DIR = r"C:\Imam\projects\cellml_reader\data"

def run_stage1(task_id: int, session: Session):
    """STAGE 1: CellML variable extraction."""
    task = session.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    if not task:
        raise ValueError(f"Task with ID {task_id} not found.")

    cellml_path = os.path.join(DATA_DIR, task.cellml_file)
    if not os.path.exists(cellml_path):
        raise FileNotFoundError(f"CellML file not found: {cellml_path}")

    print(f"[{task.cellml_file}] STAGE 1: Reading variables from CellML...")
    variables = read_cellml(cellml_path)
    
    # Filter by unit
    filtered_vars = []
    unit_filters = task.unit_filters or []
    for var in variables:
        if not unit_filters or var["unit"] in unit_filters:
            filtered_vars.append(var)

    print(f"  Found {len(variables)} variables, {len(filtered_vars)} matched filters: {unit_filters}")

    # Register variables in DB
    for var in filtered_vars:
        # Check if variable run already exists
        existing = session.query(VariableRun).filter(
            VariableRun.task_id == task_id,
            VariableRun.variable_name == var["variable"],
            VariableRun.component_name == var["component"]
        ).first()

        if not existing:
            var_run = VariableRun(
                task_id=task_id,
                variable_name=var["variable"],
                component_name=var["component"],
                status="p1_done",
                process1_data=var
            )
            session.add(var_run)
        else:
            # If it already exists, reset if it was in pending
            if existing.status == "pending":
                existing.status = "p1_done"
                existing.process1_data = var

    task.status = "running"
    task.started_at = datetime.now()
    session.commit()
    print("  STAGE 1 completed successfully.")


def get_or_create_pdf_document(pdf_file_name: str, session: Session) -> PdfDocument:
    """Load text from PDF and save sentences to DB if not already cached."""
    pdf_doc = session.query(PdfDocument).filter(PdfDocument.file_name == pdf_file_name).first()
    if pdf_doc:
        print(f"  [PDF Cache Hit] Document '{pdf_file_name}' already exists in database.")
        return pdf_doc

    pdf_path = os.path.join(DATA_DIR, pdf_file_name)
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    print(f"  [PDF Cache Miss] Extracting text from PDF: {pdf_file_name}...")
    full_text = extract_pdf_text(pdf_path)
    sentences = split_sentences(full_text)
    
    pdf_doc = PdfDocument(
        file_name=pdf_file_name,
        full_text=full_text
    )
    session.add(pdf_doc)
    session.commit()  # commit to get pdf_doc.id

    print(f"  Saving {len(sentences)} sentences to database...")
    sentences_batch = []
    for idx, sent in enumerate(sentences):
        sentences_batch.append(
            PdfSentence(
                pdf_id=pdf_doc.id,
                sentence_index=idx,
                sentence_text=sent
            )
        )
    session.bulk_save_objects(sentences_batch)
    session.commit()
    return pdf_doc


def run_stage2_variable(task_id: int, var_run_id: int, session: Session):
    """STAGE 2: Generate synonyms via LLM and match context from PDF for a specific variable."""
    task = session.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    var_run = session.query(VariableRun).filter(VariableRun.id == var_run_id).first()
    if not task or not var_run:
        raise ValueError("Task or VariableRun not found.")

    print(f"  [{var_run.variable_name}] STAGE 2: Generating synonyms & searching PDF...")
    
    # 1. Get/Cache PDF text
    pdf_doc = get_or_create_pdf_document(task.pdf_file, session)
    
    # 2. Get PDF sentences
    db_sentences = session.query(PdfSentence).filter(PdfSentence.pdf_id == pdf_doc.id).order_by(PdfSentence.sentence_index).all()
    sentence_texts = [s.sentence_text for s in db_sentences]

    # 3. Generate synonyms via LLM
    variable_info = var_run.process1_data
    synonyms = generate_synonyms(variable_info, task.paper_title)

    # 4. Search contexts
    matches = search_context(sentence_texts, synonyms)
    print(f"    Found {len(matches)} matching evidence sentences.")

    # 5. Save results
    var_run.process2_data = {
        "variable": var_run.variable_name,
        "component": var_run.component_name,
        "unit": variable_info.get("unit"),
        "synonyms": synonyms,
        "contexts": matches
    }
    var_run.status = "p2_done"
    var_run.error_message = None
    session.commit()
    print(f"    STAGE 2 completed for {var_run.variable_name}.")


def run_stage3_variable(task_id: int, var_run_id: int, session: Session):
    """STAGE 3: Run LLM Process 3 biological annotation and map ontologies via database query."""
    task = session.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    var_run = session.query(VariableRun).filter(VariableRun.id == var_run_id).first()
    if not task or not var_run:
        raise ValueError("Task or VariableRun not found.")

    print(f"  [{var_run.variable_name}] STAGE 3: Inferring biological process and ontologies lookup...")
    p2_data = var_run.process2_data
    if not p2_data:
        raise ValueError("Stage 2 data is missing. Run Stage 2 first.")

    contexts = p2_data.get("contexts", [])
    evidence_sentences = [ctx["sentence"] for ctx in contexts[:5]]

    # If no evidence, immediately set to completed with a blank fallback annotation
    if not evidence_sentences:
        print("    Tidak ada kalimat evidence, skipping LLM and writing blank annotation.")
        fallback = {
            "name": var_run.component_name.replace("_", " ").title(),
            "component": var_run.component_name,
            "current_variable": var_run.variable_name,
            "mediator": "NOT_FOUND",
            "mediator_ontology_keywords": [],
            "participants": [
                {
                    "ion": "NOT_FOUND",
                    "ion_ontology_keywords": [],
                    "source": "NOT_FOUND",
                    "source_ontology_keywords": [],
                    "sink": "NOT_FOUND",
                    "sink_ontology_keywords": [],
                    "ion_ontology_id": "NOT_FOUND",
                    "source_ontology_id": "NOT_FOUND",
                    "sink_ontology_id": "NOT_FOUND"
                }
            ],
            "mediator_ontology_id": "NOT_FOUND"
        }
        var_run.process3_data = fallback
        var_run.status = "completed"
        var_run.error_message = None
        session.commit()
        return

    # Build prompt
    evidence_text = "\n".join(f"- {s}" for s in evidence_sentences)
    component_title = var_run.component_name.replace('_', ' ').title()
    prompt = PROCESS3_PROMPT_TEMPLATE.format(
        variable_name=var_run.variable_name,
        unit=p2_data.get("unit", ""),
        component=var_run.component_name,
        component_title=component_title,
        evidence_sentences=evidence_text
    )

    # Call LLM (with cache check)
    try:
        cached_content = check_llm_cache(prompt)
        if cached_content:
            content = cached_content
            print("    [LLM Cache Hit] Menggunakan response anotasi dari cache.")
        else:
            response = ollama.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}]
            )
            content = response["message"]["content"]
            save_llm_cache(prompt, content)

        json_text = _extract_json(content)
        llm_result = json.loads(json_text)
        
        # Query ontology in database
        annotation = enrich_with_ontology_db(session, llm_result)
        
        var_run.process3_data = annotation
        var_run.status = "completed"
        var_run.error_message = None
        
    except Exception as e:
        var_run.status = "failed"
        var_run.error_message = str(e)
        print(f"    ERROR STAGE 3: {e}")
        
    session.commit()
    print(f"    STAGE 3 completed for {var_run.variable_name}.")


def run_pipeline_for_task(task_id: int):
    """Run all stages sequentially for a specific task using a clean DB session."""
    with get_session() as session:
        task = session.query(PipelineTask).filter(PipelineTask.id == task_id).first()
        if not task:
            raise ValueError(f"Task with ID {task_id} not found.")

        # Stage 1: CellML Extraction
        if task.status in ["pending", "failed"]:
            run_stage1(task_id, session)

        # Stage 2 & 3: Run for each variable
        variables = session.query(VariableRun).filter(VariableRun.task_id == task_id).all()
        for var in variables:
            # Stage 2: Sinonim & PDF context
            if var.status in ["pending", "p1_done", "failed"]:
                try:
                    run_stage2_variable(task_id, var.id, session)
                except Exception as e:
                    var.status = "failed"
                    var.error_message = f"Stage 2 failed: {e}"
                    session.commit()
                    continue

            # Stage 3: LLM Anotasi & Ontologi
            if var.status == "p2_done":
                try:
                    run_stage3_variable(task_id, var.id, session)
                except Exception as e:
                    var.status = "failed"
                    var.error_message = f"Stage 3 failed: {e}"
                    session.commit()

        # Check overall status
        failed_count = session.query(VariableRun).filter(
            VariableRun.task_id == task_id,
            VariableRun.status == "failed"
        ).count()
        
        pending_count = session.query(VariableRun).filter(
            VariableRun.task_id == task_id,
            VariableRun.status != "completed"
        ).count()

        if pending_count == 0:
            task.status = "completed"
            task.completed_at = datetime.now()
        elif failed_count > 0:
            task.status = "failed"
        
        session.commit()
        print(f"\nTask {task_id} finished. Status: {task.status}")
