import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Annotated

from core.database import get_db
from core import schemas
from core.security import hash_password, verify_password, create_access_token
from core.auth_deps import get_current_student
from models import models

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


def _derive_username(email: str) -> str:
    """Derive a username from the email local part."""
    base = (email.split("@", 1)[0] or "student").lower()
    return _clean_username(base)


def _clean_username(value: str) -> str:
    value = value.strip().lower()
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return (cleaned or "student")[:50]


def _ensure_unique_username(db: Session, desired: str, exclude_id: uuid.UUID | None = None) -> str:
    """Return desired username if available, otherwise append a short suffix."""
    existing = db.query(models.Student).filter(models.Student.username == desired).first()
    if not existing or (exclude_id and existing.id == exclude_id):
        return desired
    suffix = "_" + uuid.uuid4().hex[:6]
    return desired[: 50 - len(suffix)] + suffix


@router.post("/register", response_model=schemas.TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: schemas.RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
):
    email = payload.email.lower().strip()

    # Check duplicate email
    if db.query(models.Student).filter(models.Student.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    # User-chosen usernames should fail loudly on conflict. Auto-derived usernames
    # should stay frictionless by receiving a short suffix when needed.
    if payload.username:
        desired_username = _clean_username(payload.username)
        if db.query(models.Student).filter(models.Student.username == desired_username).first():
            raise HTTPException(status_code=409, detail=f"Username '{desired_username}' is already taken")
    else:
        desired_username = _ensure_unique_username(db, _derive_username(email))

    now = datetime.now(timezone.utc)
    student = models.Student(
        id=uuid.uuid4(),
        email=email,
        password_hash=hash_password(payload.password),
        username=desired_username,
        display_name=payload.display_name,
        major=payload.major,
        student_year=payload.student_year,
        created_at=now,
        updated_at=now,
    )
    db.add(student)
    db.commit()
    db.refresh(student)

    token = create_access_token(student.id)
    return schemas.TokenResponse(
        access_token=token,
        student_id=student.id,
        email=student.email,
        username=student.username,
        display_name=student.display_name,
    )


@router.post("/login", response_model=schemas.TokenResponse)
def login(
    payload: schemas.LoginRequest,
    db: Annotated[Session, Depends(get_db)],
):
    email = payload.email.lower().strip()
    student = db.query(models.Student).filter(models.Student.email == email).first()
    if not student:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(payload.password, student.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(student.id)
    return schemas.TokenResponse(
        access_token=token,
        student_id=student.id,
        email=student.email,
        username=student.username,
        display_name=student.display_name,
    )


@router.get("/me", response_model=schemas.MeResponse)
def me(current_student: Annotated[models.Student, Depends(get_current_student)]):
    return schemas.MeResponse(
        id=current_student.id,
        email=current_student.email,
        username=current_student.username,
        display_name=current_student.display_name,
        major=current_student.major,
        student_year=current_student.student_year,
    )
