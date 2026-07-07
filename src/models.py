from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass

class OntologyTerm(Base):
    __tablename__ = "ontology_terms"
    
    id = Column(String, primary_key=True)
    name = Column(String, index=True, nullable=False)
    synonyms = Column(Text, nullable=True)  # Comma-separated or JSON list of synonyms
    ontology_type = Column(String, index=True, nullable=False)  # "chebi" or "go"

class PdfDocument(Base):
    __tablename__ = "pdf_documents"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_name = Column(String, unique=True, index=True, nullable=False)
    paper_title = Column(String, nullable=True)
    full_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    
    # Relationship to sentences (Cascade Delete)
    sentences = relationship("PdfSentence", back_populates="document", cascade="all, delete-orphan")

class PdfSentence(Base):
    __tablename__ = "pdf_sentences"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    pdf_id = Column(Integer, ForeignKey("pdf_documents.id", ondelete="CASCADE"), nullable=False)
    sentence_index = Column(Integer, nullable=False)
    sentence_text = Column(Text, nullable=False)
    
    document = relationship("PdfDocument", back_populates="sentences")

class PipelineTask(Base):
    __tablename__ = "pipeline_tasks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    cellml_file = Column(String, nullable=False)
    pdf_file = Column(String, nullable=False)
    paper_title = Column(String, nullable=False)
    unit_filters = Column(JSON, nullable=True)  # List of filtered units e.g., ["microA_per_cm2"]
    status = Column(String, default="pending", index=True, nullable=False)  # "pending", "running", "completed", "failed"
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationship to variables (Cascade Delete)
    variables = relationship("VariableRun", back_populates="task", cascade="all, delete-orphan")

class VariableRun(Base):
    __tablename__ = "variable_runs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("pipeline_tasks.id", ondelete="CASCADE"), nullable=False)
    variable_name = Column(String, nullable=False)
    component_name = Column(String, nullable=False)
    status = Column(String, default="pending", index=True, nullable=False)  # "pending", "p1_done", "p2_done", "completed", "failed"
    
    process1_data = Column(JSON, nullable=True)  # CellML variables info
    process2_data = Column(JSON, nullable=True)  # LLM synonyms & matching contexts
    process3_data = Column(JSON, nullable=True)  # Final annotation JSON
    
    error_message = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    task = relationship("PipelineTask", back_populates="variables")

class LlmCache(Base):
    __tablename__ = "llm_cache"
    
    prompt_hash = Column(String, primary_key=True)
    prompt_content = Column(Text, nullable=False)
    response_content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now())
