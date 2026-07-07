import sys
from src.database import get_session, run_housekeeping
from src.models import PipelineTask
from src.pipeline import run_pipeline_for_task

def main():
    print("============================================================")
    print("CELLML READER PIPELINE (DATABASE RUN)")
    print("============================================================")
    
    # 1. Run housekeeping to clear data older than 7 days
    try:
        run_housekeeping(days=7)
    except Exception as e:
        print(f"[Warning] Housekeeping failed: {e}")

    # 2. Get pending or failed tasks from database
    with get_session() as session:
        tasks = session.query(PipelineTask).filter(
            PipelineTask.status.in_(["pending", "failed"])
        ).order_by(PipelineTask.id).all()
        
        task_ids = [t.id for t in tasks]

    if not task_ids:
        print("No pending or failed tasks found in database. Exiting.")
        return

    print(f"Found {len(task_ids)} active tasks in database to process.")

    # 3. Process each task sequentially
    for idx, task_id in enumerate(task_ids):
        print(f"\nProcessing Task {idx+1}/{len(task_ids)} (ID: {task_id})...")
        try:
            run_pipeline_for_task(task_id)
        except Exception as e:
            print(f"ERROR processing Task {task_id}: {e}")

    print("\nAll pipeline tasks processed!")

if __name__ == "__main__":
    main()
