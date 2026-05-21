from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Annotated

from core.database import SessionLocal, get_db
from core.auth_deps import get_current_student
from core.schemas import AIGeneratedPlan, GeneratePlanResponse, build_fallback_ai_plan
from models import models
from services.ai_engine import OllamaError, generate_academic_plan

router = APIRouter(prefix="/api/v1/ai", tags=["AI"])


def _safe_plan_from_db(value: object) -> AIGeneratedPlan | None:
    if not value:
        return None
    try:
        return AIGeneratedPlan.model_validate(value)
    except Exception:
        return build_fallback_ai_plan()


def _response_from_context(
    student_id,
    context: models.StudentContext,
    message: str,
) -> GeneratePlanResponse:
    status = context.ai_status if context.ai_status in {"EMPTY", "PENDING", "COMPLETED", "FAILED"} else "FAILED"
    return GeneratePlanResponse(
        status=status,
        student_id=student_id,
        ai_plan=_safe_plan_from_db(context.ai_plan),
        ai_last_error=context.ai_last_error,
        message=message,
    )


def _commit_or_500(db: Session, action: str) -> None:
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database update failed while {action}: {str(e)}")


async def _run_generation_job(student_id) -> None:
    """In-process MVP background job.

    This is not a durable queue. If the backend restarts during generation, the row
    can remain PENDING and the user should click regenerate.
    """
    db = SessionLocal()
    try:
        context = (
            db.query(models.StudentContext)
            .filter(models.StudentContext.student_id == student_id)
            .first()
        )
        if not context:
            return

        try:
            plan = await generate_academic_plan(context.raw_input)
        except OllamaError as e:
            fallback = build_fallback_ai_plan()
            context.ai_plan = fallback.model_dump()
            context.ai_status = "FAILED"
            context.ai_last_error = str(e)[:4000]
            context.updated_at = datetime.now(timezone.utc)
            db.commit()
            return

        context.ai_plan = plan.model_dump()
        context.ai_status = "COMPLETED"
        context.ai_last_error = None
        context.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as e:
        db.rollback()
        context = (
            db.query(models.StudentContext)
            .filter(models.StudentContext.student_id == student_id)
            .first()
        )
        if context:
            fallback = build_fallback_ai_plan()
            context.ai_plan = fallback.model_dump()
            context.ai_status = "FAILED"
            context.ai_last_error = f"Unexpected AI job failure: {str(e)}"[:4000]
            context.updated_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/generate_academic_plan", response_model=GeneratePlanResponse)
async def api_generate_academic_plan(
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    current_student: Annotated[models.Student, Depends(get_current_student)],
):
    """Start AI academic-plan generation for the authenticated student.

    The endpoint returns immediately with PENDING. The actual Ollama request runs
    in an in-process background task so the frontend can poll status without holding
    a long HTTP request open.
    """
    context = (
        db.query(models.StudentContext)
        .filter(models.StudentContext.student_id == current_student.id)
        .first()
    )

    if not context:
        raise HTTPException(
            status_code=400,
            detail="No student context found. Sync your academic profile first.",
        )

    if not context.raw_input or context.raw_input == {}:
        raise HTTPException(
            status_code=400,
            detail="Student context raw_input is empty. Complete your academic profile and sync first.",
        )

    if context.ai_status == "PENDING":
        return _response_from_context(
            current_student.id,
            context,
            "Academic plan generation is already running.",
        )

    context.ai_status = "PENDING"
    context.ai_last_error = None
    context.updated_at = datetime.now(timezone.utc)
    _commit_or_500(db, "marking AI plan generation as pending")

    background_tasks.add_task(_run_generation_job, current_student.id)
    return _response_from_context(
        current_student.id,
        context,
        "Academic plan generation started.",
    )


@router.get("/academic_plan_status", response_model=GeneratePlanResponse)
def api_academic_plan_status(
    db: Annotated[Session, Depends(get_db)],
    current_student: Annotated[models.Student, Depends(get_current_student)],
):
    """Return the latest AI plan generation state for the authenticated student."""
    context = (
        db.query(models.StudentContext)
        .filter(models.StudentContext.student_id == current_student.id)
        .first()
    )

    if not context:
        raise HTTPException(
            status_code=400,
            detail="No student context found. Sync your academic profile first.",
        )

    return _response_from_context(
        current_student.id,
        context,
        "Academic plan status loaded.",
    )
