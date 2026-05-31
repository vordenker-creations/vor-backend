from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from uuid import UUID
from typing import Optional, Any, Literal


# ---------------------------------------------------------
# Auth Schemas
# ---------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    username: Optional[str] = Field(default=None, max_length=50)
    display_name: Optional[str] = None
    major: Optional[str] = None
    student_year: int = Field(default=1, ge=1, le=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    student_id: UUID
    email: str
    username: str
    display_name: Optional[str] = None


class MeResponse(BaseModel):
    id: UUID
    email: str
    username: str
    display_name: Optional[str] = None
    major: Optional[str] = None
    student_year: int


# ---------------------------------------------------------
# Sync Schemas (kept from previous phase)
# ---------------------------------------------------------

class StudentSync(BaseModel):
    """Profile fields the client may update through /sync.
    password_hash is accepted but ignored — password changes
    must go through a dedicated auth endpoint later."""
    id: UUID
    email: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    major: Optional[str] = None
    student_year: int = Field(default=1, ge=1, le=8)
    password_hash: Optional[str] = None  # deprecated for sync, kept for compat
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ContextSync(BaseModel):
    id: UUID
    student_id: UUID
    raw_input: dict[str, Any] | str
    ai_plan: Optional[dict[str, Any] | str] = None
    ai_status: str = "EMPTY"
    ai_last_error: Optional[str] = None
    updated_at: Optional[datetime] = None


class SyncAllRequest(BaseModel):
    students: list[StudentSync] = []
    student_context: list[ContextSync] = []


# ---------------------------------------------------------
# Generic response
# ---------------------------------------------------------

class SyncResponse(BaseModel):
    status: str
    message: str
    record_id: Optional[UUID] = None


# ---------------------------------------------------------
# AI Plan Schemas (Phase 5)
# ---------------------------------------------------------

class StrictAIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillAnalysis(StrictAIModel):
    strengths: list[str]
    weaknesses: list[str]
    recommended_focus: list[str]


class StudyTask(StrictAIModel):
    title: str
    type: Literal["study", "practice", "review", "project"]
    duration_min: int = Field(ge=1, le=480)
    priority: Literal["high", "medium", "low"]


class DayPlan(StrictAIModel):
    day: str
    tasks: list[StudyTask]


class RoadmapItem(StrictAIModel):
    title: str
    status: Literal["completed", "in_progress", "not_started"]
    progress_pct: int = Field(ge=0, le=100)
    ai_insight: str


class PlanMetrics(StrictAIModel):
    academic_progress: int = Field(ge=0, le=100)
    career_readiness: int = Field(ge=0, le=100)
    task_load: int = Field(ge=0, le=100)


class AIGeneratedPlan(StrictAIModel):
    """Strict schema for AI-generated academic plans.
    All five root keys must be present. Lists may be empty."""
    student_summary: str
    skill_analysis: SkillAnalysis
    weekly_study_plan: list[DayPlan]
    academic_roadmap: list[RoadmapItem]
    metrics: PlanMetrics


AIStatus = Literal["EMPTY", "PENDING", "COMPLETED", "FAILED"]


class GeneratePlanResponse(BaseModel):
    status: AIStatus
    student_id: UUID
    ai_plan: Optional[AIGeneratedPlan] = None
    ai_last_error: Optional[str] = None
    message: str


def build_fallback_ai_plan() -> AIGeneratedPlan:
    """Safe empty plan used when Ollama fails or returns invalid JSON."""
    return AIGeneratedPlan(
        student_summary="",
        skill_analysis=SkillAnalysis(
            strengths=[],
            weaknesses=[],
            recommended_focus=[],
        ),
        weekly_study_plan=[],
        academic_roadmap=[],
        metrics=PlanMetrics(
            academic_progress=0,
            career_readiness=0,
            task_load=0,
        ),
    )


class StudentContextResponse(BaseModel):
    id: UUID
    student_id: UUID
    raw_input: dict[str, Any]
    ai_status: AIStatus
    ai_plan: Optional[dict[str, Any]] = None
    ai_last_error: Optional[str] = None
    updated_at: Optional[datetime] = None


class SyncContextPullResponse(BaseModel):
    student: MeResponse
    context: StudentContextResponse

