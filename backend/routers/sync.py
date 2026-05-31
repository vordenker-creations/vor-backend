from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json
import logging
import uuid
from typing import Annotated

from core.database import get_db
from core.auth_deps import get_current_student
from models import models
from core import schemas

router = APIRouter(prefix="/api/v1/sync", tags=["Sync"])
logger = logging.getLogger("sync_router")


def _parse_json_payload(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _normalize_username(username: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in "._-" else "_" for ch in username.strip().lower()
    )
    return (cleaned or "student")[:50]


def _is_meaningful_context_payload(value, key=None) -> bool:
    if value is None:
        return False

    # Define metadata/scaffold keys that are NOT meaningful on their own
    metadata_keys = {
        "day", "time", "period", "day_of_week", "hour", "minutes",
        "current_semester", "student_year", "semester", "year"
    }

    if isinstance(value, str):
        val = value.strip()
        if not val:
            return False
        if key in metadata_keys:
            return False
        return True

    if isinstance(value, (int, float)):
        # GPA > 0 is meaningful. Other numeric scaffold keys (semester, year, etc.) are NOT.
        if key in ("gpa", "GPA"):
            return value > 0
        if key in metadata_keys:
            return False
        # Any other unexpected numeric key
        return value > 0

    if isinstance(value, list):
        return any(_is_meaningful_context_payload(item, key) for item in value)

    if isinstance(value, dict):
        # Check if this is a timetable row / block (identified by having day/time/period keys)
        is_timetable_row = any(k in value for k in ("day", "time", "period", "day_of_week"))
        if is_timetable_row:
            # Must have a real course/title/subject/name with meaningful content
            course_keys = ("course", "subject", "title", "name", "course_name")
            return any(
                k in value and _is_meaningful_context_payload(value[k], k)
                for k in course_keys
            )

        # General dict: check if any of the key-value pairs is meaningful
        return any(_is_meaningful_context_payload(v, k) for k, v in value.items())

    return False


@router.get("/context", response_model=schemas.SyncContextPullResponse)
def get_sync_context(
    db: Annotated[Session, Depends(get_db)],
    current_student: Annotated[models.Student, Depends(get_current_student)],
):
    logger.info(f"Student {current_student.id}: GET /api/v1/sync/context request authenticated")

    context = (
        db.query(models.StudentContext)
        .filter(models.StudentContext.student_id == current_student.id)
        .first()
    )

    if not context:
        context = models.StudentContext(
            id=uuid.uuid4(),
            student_id=current_student.id,
            raw_input={},
            ai_plan=None,
            ai_status="EMPTY",
            ai_last_error=None,
        )
        db.add(context)
        try:
            db.commit()
            db.refresh(context)
            logger.info(f"Student {current_student.id}: Created default context (ai_status=EMPTY)")
        except Exception as e:
            db.rollback()
            logger.error(f"Student {current_student.id}: Failed to create default context: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to initialize context: {e}")
    else:
        logger.info(f"Student {current_student.id}: Loaded existing context (ai_status={context.ai_status})")

    student_profile = schemas.MeResponse(
        id=current_student.id,
        email=current_student.email,
        username=current_student.username,
        display_name=current_student.display_name,
        major=current_student.major,
        student_year=current_student.student_year
    )

    context_response = schemas.StudentContextResponse(
        id=context.id,
        student_id=context.student_id,
        raw_input=context.raw_input,
        ai_status=context.ai_status,
        ai_plan=context.ai_plan,
        ai_last_error=context.ai_last_error,
        updated_at=context.updated_at
    )

    return schemas.SyncContextPullResponse(
        student=student_profile,
        context=context_response
    )


@router.post("")
def sync_all(
    payload: schemas.SyncAllRequest,
    db: Annotated[Session, Depends(get_db)],
    current_student: Annotated[models.Student, Depends(get_current_student)],
):
    logger.info(f"Student {current_student.id}: POST /api/v1/sync request authenticated")
    try:
        for s in payload.students:
            if s.id != current_student.id:
                raise HTTPException(
                    status_code=403,
                    detail=f"Token owner {current_student.id} cannot sync student {s.id}",
                )

            if s.username:
                next_username = _normalize_username(s.username)
                existing_username = (
                    db.query(models.Student)
                    .filter(models.Student.username == next_username)
                    .filter(models.Student.id != current_student.id)
                    .first()
                )
                if existing_username:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Username '{next_username}' is already taken",
                    )
                current_student.username = next_username
            if s.display_name:
                current_student.display_name = s.display_name
            if s.major is not None:
                current_student.major = s.major
            if s.student_year:
                current_student.student_year = s.student_year
            if s.updated_at:
                current_student.updated_at = s.updated_at
            logger.info(f"Student {current_student.id}: updated profile fields on sync")

        for c in payload.student_context:
            if c.student_id != current_student.id:
                raise HTTPException(
                    status_code=403,
                    detail=f"Token owner {current_student.id} cannot sync context for student {c.student_id}",
                )

            raw_input_data = _parse_json_payload(c.raw_input)

            existing_c = (
                db.query(models.StudentContext)
                .filter(models.StudentContext.student_id == current_student.id)
                .first()
            )
            if existing_c:
                incoming_meaningful = _is_meaningful_context_payload(raw_input_data)
                server_meaningful = _is_meaningful_context_payload(existing_c.raw_input)
                
                if not incoming_meaningful and server_meaningful:
                    logger.info(
                        f"Student {current_student.id}: incoming raw_input is empty/not meaningful, ignoring overwrite to protect existing server raw_input"
                    )
                else:
                    existing_c.raw_input = raw_input_data
                    logger.info(
                        f"Student {current_student.id}: accepted raw_input (meaningful={incoming_meaningful})"
                    )
                
                logger.info(
                    f"Student {current_student.id}: preserving existing server ai_status={existing_c.ai_status}"
                )
                if c.updated_at:
                    existing_c.updated_at = c.updated_at
            else:
                raw_input_data = raw_input_data if _is_meaningful_context_payload(raw_input_data) else {}
                new_context = models.StudentContext(
                    id=c.id,
                    student_id=current_student.id,
                    raw_input=raw_input_data,
                    ai_plan=None,
                    ai_status="EMPTY",
                    ai_last_error=None,
                )
                if c.updated_at:
                    new_context.updated_at = c.updated_at
                db.add(new_context)
                logger.info(f"Student {current_student.id}: initialized new context on sync")

        db.commit()
        return {"status": "success", "message": "Batch synced successfully"}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")
