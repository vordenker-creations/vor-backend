from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json
from typing import Annotated

from core.database import get_db
from core.auth_deps import get_current_student
from models import models
from core import schemas

router = APIRouter(prefix="/api/v1/sync", tags=["Sync"])


def _parse_json_payload(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _truncate_error(error: str | None) -> str | None:
    return error[:4000] if error else None


def _normalize_username(username: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in username.strip().lower())
    return (cleaned or "student")[:50]


@router.post("")
def sync_all(
    payload: schemas.SyncAllRequest,
    db: Annotated[Session, Depends(get_db)],
    current_student: Annotated[models.Student, Depends(get_current_student)],
):
    """
    Authenticated sync endpoint.
    Receives dirty records from the client and upserts them,
    but only for the student who owns the token.
    """
    try:
        # --------------------------------------------------
        # 1. Sync Student profile fields (ownership check)
        # --------------------------------------------------
        for s in payload.students:
            if s.id != current_student.id:
                raise HTTPException(
                    status_code=403,
                    detail=f"Token owner {current_student.id} cannot sync student {s.id}",
                )

            # Update mutable profile fields only; never password_hash.
            if s.username:
                next_username = _normalize_username(s.username)
                existing_username = (
                    db.query(models.Student)
                    .filter(models.Student.username == next_username)
                    .filter(models.Student.id != current_student.id)
                    .first()
                )
                if existing_username:
                    raise HTTPException(status_code=409, detail=f"Username '{next_username}' is already taken")
                current_student.username = next_username
            if s.display_name:
                current_student.display_name = s.display_name
            if s.major is not None:
                current_student.major = s.major
            if s.student_year:
                current_student.student_year = s.student_year
            if s.updated_at:
                current_student.updated_at = s.updated_at

        # --------------------------------------------------
        # 2. Sync Contexts (ownership check)
        # --------------------------------------------------
        for c in payload.student_context:
            if c.student_id != current_student.id:
                raise HTTPException(
                    status_code=403,
                    detail=f"Token owner {current_student.id} cannot sync context for student {c.student_id}",
                )

            raw_input_data = _parse_json_payload(c.raw_input)
            ai_plan_data = _parse_json_payload(c.ai_plan) if c.ai_plan else None
            ai_last_error = _truncate_error(c.ai_last_error)

            existing_c = (
                db.query(models.StudentContext)
                .filter(models.StudentContext.student_id == current_student.id)
                .first()
            )
            if existing_c:
                existing_c.raw_input = raw_input_data
                # Do not overwrite existing_c.ai_status, existing_c.ai_plan, or existing_c.ai_last_error from the client during normal profile sync.
                if c.updated_at:
                    existing_c.updated_at = c.updated_at
            else:
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

        db.commit()
        return {"status": "success", "message": "Batch synced successfully"}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")
