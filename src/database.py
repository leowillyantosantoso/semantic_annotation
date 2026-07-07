import hashlib
from datetime import datetime, timedelta
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, PipelineTask, VariableRun, PdfDocument, PdfSentence, LlmCache, OntologyTerm

DATABASE_URL = "postgresql://ml_user:ml_user@127.0.0.1:5432/cellml_reader"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@contextmanager
def get_session():
    """Context manager for SQLAlchemy database session."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def get_db():
    """Dependency generator for FastAPI dependency injection."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def init_db():
    """Create all tables in the database if they do not exist."""
    Base.metadata.create_all(bind=engine)

# ==============================================================================
# LLM CACHE HELPERS
# ==============================================================================
def get_prompt_hash(prompt: str) -> str:
    """Generate MD5 hash of a prompt string."""
    return hashlib.md5(prompt.encode("utf-8")).hexdigest()

def check_llm_cache(prompt: str) -> str | None:
    """Retrieve LLM response from cache if it exists, otherwise return None."""
    p_hash = get_prompt_hash(prompt)
    with get_session() as session:
        cache_item = session.query(LlmCache).filter(LlmCache.prompt_hash == p_hash).first()
        if cache_item:
            return cache_item.response_content
    return None

def save_llm_cache(prompt: str, response: str):
    """Save LLM prompt and response to cache."""
    p_hash = get_prompt_hash(prompt)
    with get_session() as session:
        # Check if already cached to avoid primary key conflict
        existing = session.query(LlmCache).filter(LlmCache.prompt_hash == p_hash).first()
        if existing:
            existing.response_content = response
        else:
            cache_item = LlmCache(
                prompt_hash=p_hash,
                prompt_content=prompt,
                response_content=response
            )
            session.add(cache_item)

# ==============================================================================
# HOUSEKEEPING HELPERS
# ==============================================================================
def run_housekeeping(days: int = 7):
    """Delete pipeline tasks and PDF documents older than specified days."""
    cutoff_date = datetime.now() - timedelta(days=days)
    with get_session() as session:
        # Cascade deletes will automatically remove associated variable_runs and pdf_sentences
        deleted_tasks = session.query(PipelineTask).filter(PipelineTask.started_at < cutoff_date).delete()
        deleted_pdfs = session.query(PdfDocument).filter(PdfDocument.created_at < cutoff_date).delete()
        print(f"[Housekeeping] Deleted {deleted_tasks} tasks and {deleted_pdfs} PDFs older than {days} days.")
