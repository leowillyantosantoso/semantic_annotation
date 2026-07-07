import os
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from src.database import get_session, get_db
from src.models import PipelineTask, VariableRun
from src.pipeline import (
    run_pipeline_for_task, 
    run_stage2_variable, 
    run_stage3_variable,
    run_stage1
)

app = FastAPI(title="CellML Pipeline Dashboard")

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)

# Helper schemas
class ReprocessRequest(BaseModel):
    type: str  # "total" or "process3"

class ReprocessVariableRequest(BaseModel):
    type: str  # "stage2" or "stage3"

# ==============================================================================
# REST API ENDPOINTS
# ==============================================================================

@app.get("/api/tasks")
def list_tasks(db: Session = Depends(get_db)):
    """List all pipeline tasks with progress statistics."""
    tasks = db.query(PipelineTask).order_by(PipelineTask.id).all()
    results = []
    
    for task in tasks:
        # Calculate stats
        total = db.query(VariableRun).filter(VariableRun.task_id == task.id).count()
        completed = db.query(VariableRun).filter(VariableRun.task_id == task.id, VariableRun.status == "completed").count()
        failed = db.query(VariableRun).filter(VariableRun.task_id == task.id, VariableRun.status == "failed").count()
        
        progress = int((completed / total) * 100) if total > 0 else 0
        
        results.append({
            "id": task.id,
            "cellml_file": task.cellml_file,
            "pdf_file": task.pdf_file,
            "paper_title": task.paper_title,
            "unit_filters": task.unit_filters,
            "status": task.status,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "stats": {
                "total": total,
                "completed": completed,
                "failed": failed,
                "progress": progress
            }
        })
    return results


@app.post("/api/tasks/{id}/run")
def trigger_task(id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Trigger pipeline execution for a specific task asynchronously."""
    task = db.query(PipelineTask).filter(PipelineTask.id == id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if task.status == "running":
        return {"status": "already_running", "message": "Task is already running."}
        
    task.status = "running"
    task.started_at = task.started_at or datetime.now()
    db.commit()
    
    background_tasks.add_task(run_pipeline_for_task, id)
    return {"status": "triggered", "message": "Pipeline run started in the background."}


@app.post("/api/tasks/{id}/reprocess")
def reprocess_task(id: int, req: ReprocessRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Reprocess a task from the beginning (total) or only LLM annotations (process3)."""
    task = db.query(PipelineTask).filter(PipelineTask.id == id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    variables = db.query(VariableRun).filter(VariableRun.task_id == id).all()
    
    if req.type == "total":
        print(f"[WebAPI] Reprocessing task {id} (TOTAL reset)...")
        task.status = "pending"
        task.started_at = None
        task.completed_at = None
        for var in variables:
            var.status = "pending"
            var.process1_data = None
            var.process2_data = None
            var.process3_data = None
            var.error_message = None
            
    elif req.type == "process3":
        print(f"[WebAPI] Reprocessing task {id} (Process 3 only)...")
        task.status = "pending"
        for var in variables:
            if var.status in ["completed", "failed", "p2_done"]:
                var.status = "p2_done"
                var.process3_data = None
                var.error_message = None
    else:
        raise HTTPException(status_code=400, detail="Invalid reprocess type. Use 'total' or 'process3'.")
        
    db.commit()
    background_tasks.add_task(run_pipeline_for_task, id)
    return {"status": "triggered", "message": f"Reprocessing ({req.type}) started in background."}


@app.get("/api/tasks/{id}/variables")
def get_task_variables(id: int, db: Session = Depends(get_db)):
    """Get all variables list and status for a specific task."""
    task = db.query(PipelineTask).filter(PipelineTask.id == id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    variables = db.query(VariableRun).filter(VariableRun.task_id == id).order_by(VariableRun.id).all()
    return [{
        "id": var.id,
        "variable_name": var.variable_name,
        "component_name": var.component_name,
        "status": var.status,
        "error_message": var.error_message,
        "updated_at": var.updated_at
    } for var in variables]


@app.get("/api/variables/{id}/details")
def get_variable_details(id: int, db: Session = Depends(get_db)):
    """Get synonyms, matched sentences, and ontology annotations for a specific variable."""
    var = db.query(VariableRun).filter(VariableRun.id == id).first()
    if not var:
        raise HTTPException(status_code=404, detail="Variable run not found")
        
    return {
        "id": var.id,
        "variable_name": var.variable_name,
        "component_name": var.component_name,
        "status": var.status,
        "process1_data": var.process1_data,
        "process2_data": var.process2_data,
        "process3_data": var.process3_data,
        "error_message": var.error_message
    }


@app.post("/api/variables/{id}/reprocess")
def reprocess_single_variable(id: int, req: ReprocessVariableRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Reprocess a single variable for synonyms/PDF matching (stage2) or only annotations (stage3)."""
    var = db.query(VariableRun).filter(VariableRun.id == id).first()
    if not var:
        raise HTTPException(status_code=404, detail="Variable not found")
        
    task = db.query(PipelineTask).filter(PipelineTask.id == var.task_id).first()
    
    if req.type == "stage2":
        var.status = "p1_done"
        var.process2_data = None
        var.process3_data = None
        var.error_message = None
    elif req.type == "stage3":
        var.status = "p2_done"
        var.process3_data = None
        var.error_message = None
    else:
        raise HTTPException(status_code=400, detail="Invalid stage type. Use 'stage2' or 'stage3'.")
        
    task.status = "running"
    db.commit()
    
    # Run the specific stages inside a background task
    def run_variable_stages(task_id, var_id, stage_type):
        with get_session() as session:
            try:
                if stage_type == "stage2":
                    run_stage2_variable(task_id, var_id, session)
                    run_stage3_variable(task_id, var_id, session)
                elif stage_type == "stage3":
                    run_stage3_variable(task_id, var_id, session)
                
                # Check if task is now completed
                v_run = session.query(VariableRun).filter(VariableRun.id == var_id).first()
                p_task = session.query(PipelineTask).filter(PipelineTask.id == task_id).first()
                
                pending_count = session.query(VariableRun).filter(
                    VariableRun.task_id == task_id, 
                    VariableRun.status != "completed"
                ).count()
                
                if pending_count == 0:
                    p_task.status = "completed"
                    p_task.completed_at = datetime.now()
                else:
                    failed_count = session.query(VariableRun).filter(
                        VariableRun.task_id == task_id, 
                        VariableRun.status == "failed"
                    ).count()
                    p_task.status = "failed" if failed_count > 0 else "running"
                session.commit()
            except Exception as e:
                print(f"[Background Variable Reprocess] Error: {e}")
                
    background_tasks.add_task(run_variable_stages, var.task_id, var.id, req.type)
    return {"status": "triggered", "message": f"Reprocessing for variable {var.variable_name} ({req.type}) started."}


# Serve SPA Page
@app.get("/")
def serve_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

# Mount static folder
app.mount("/static", StaticFiles(directory=static_dir), name="static")
