from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
import uuid
import fitz  # PyMuPDF
from typing import Annotated, cast

# Explicit module resolution to the core and services directories
from core.database import get_db
from core import schemas
from models import models
from services.ai_engine import generate_skill_vector

router = APIRouter(prefix="/api/v1/students", tags=["Students"])


# ==========================================
# ENDPOINT 1: The Offline JSON Synchronizer
# ==========================================
@router.post("/sync-context", response_model=schemas.SyncResponse)
def sync_student_context(
    payload: schemas.ContextSyncRequest, db: Annotated[Session, Depends(get_db)]
):
    """
    Receives an offline-created JSON payload from the PyQt6 SQLite outbox and writes it to PostgreSQL.
    """
    try:
        # Renamed models.CV to models.StudentContext
        existing_context = (
            db.query(models.StudentContext)
            .filter(models.StudentContext.id == payload.id)
            .first()
        )

        if existing_context:
            existing_context.raw_input = payload.raw_input
        else:
            new_context = models.StudentContext(
                id=payload.id,
                student_id=payload.student_id,
                raw_input=payload.raw_input,
            )
            db.add(new_context)

        db.commit()
        return schemas.SyncResponse(
            status="success",
            message="Student context synced to Global Database.",
            record_id=payload.id,
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Database synchronization failed: {str(e)}"
        )


# ==========================================
# ENDPOINT 2: The AI Vectorization Pipeline
# ==========================================
@router.post("/{student_id}/upload-cv")
async def process_and_vectorize_cv(
    student_id: uuid.UUID,
    cv_file: Annotated[UploadFile, File(...)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Receives a physical PDF, extracts text in RAM, fetches a 768-D vector from the Mac inference node,
    and atomically commits both the Context and the Embedding to PostgreSQL.
    """
    # 1. Security Check: Is it a PDF?
    if cv_file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    # 2. Extract Text in RAM
    pdf_bytes = await cv_file.read()
    pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")

    extracted_text = "\n".join(
        cast(str, page.get_text("text")) for page in pdf_document
    )
    pdf_document.close()

    if not extracted_text.strip():
        raise HTTPException(
            status_code=400,
            detail="Could not extract readable text from the provided PDF.",
        )

    # 3. Network Handoff: Request vector from Mac's Ollama instance over Tailscale
    vector_array = await generate_skill_vector(extracted_text)

    try:
        # 4. Dual Staging: Prepare both the raw JSONB data and the mathematical Vector
        existing_context = db.query(models.StudentContext).filter(models.StudentContext.student_id == student_id).first()
        if existing_context:
            existing_context.raw_input = {"raw_text": extracted_text}
        else:
            new_context = models.StudentContext(
                student_id=student_id, raw_input={"raw_text": extracted_text}
            )
            db.add(new_context)

        existing_embedding = db.query(models.StudentEmbedding).filter(models.StudentEmbedding.student_id == student_id).first()
        if existing_embedding:
            existing_embedding.skill_vector = vector_array
        else:
            new_embedding = models.StudentEmbedding(
                student_id=student_id, skill_vector=vector_array
            )
            db.add(new_embedding)

        # 5. Atomic Commit: Write everything to PostgreSQL simultaneously
        db.commit()
        return {
            "status": "success",
            "message": "CV successfully processed, vectorized, and anchored.",
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Database pipeline failure: {str(e)}"
        )