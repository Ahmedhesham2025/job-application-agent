from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnswerCategory(StrEnum):
    IDENTITY = "identity"
    CONTACT = "contact"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    SKILLS = "skills"
    AVAILABILITY = "availability"
    WORK_AUTHORIZATION = "work_authorization"
    SPONSORSHIP = "sponsorship"
    SALARY = "salary"
    RELOCATION = "relocation"
    DEMOGRAPHIC = "demographic"
    DISABILITY = "disability"
    VETERAN_STATUS = "veteran_status"
    CRIMINAL_HISTORY = "criminal_history"
    LEGAL_ATTESTATION = "legal_attestation"
    SECURITY_CLEARANCE = "security_clearance"
    ASSESSMENT = "assessment"
    RECORDED_INTERVIEW = "recorded_interview"
    PREFERENCE = "preference"
    CONSENT = "consent"
    OPEN_ANSWER = "open_answer"
    UNKNOWN = "unknown"


class JobPosting(StrictModel):
    source: str
    source_job_id: str
    company: str
    title: str
    location: str = ""
    workplace_type: str | None = None
    description: str
    application_url: HttpUrl
    salary_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobDecision(StrictModel):
    source_job_id: str
    qualifies: bool
    match_score: int = Field(ge=0, le=100)
    matched_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    exclusion_reasons: list[str] = Field(default_factory=list)
    concise_reason: str


class ApplicationAnswer(StrictModel):
    field_name: str
    question: str
    value: str | int | float | bool | None
    fact_id: str | None
    category: AnswerCategory


class ApplicationDraft(StrictModel):
    cover_letter: str | None = None
    answers: list[ApplicationAnswer]


class ApplicationPlan(StrictModel):
    candidate_id: str
    source_job_id: str
    company: str
    title: str
    application_url: HttpUrl
    match_score: int = Field(ge=0, le=100)
    resume_path: str
    resume_sha256: str
    cover_letter: str | None = None
    answers: list[ApplicationAnswer]


class PolicyVerdict(StrictModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


class SubmissionReceipt(StrictModel):
    status: str
    source_job_id: str
    company: str
    application_url: str
    idempotency_key: str
    submitted_at: str | None = None
    confirmation: str | None = None
    artifacts: list[str] = Field(default_factory=list)


class FormAnswer(StrictModel):
    """One answer for one question found on a live application form."""

    key: str
    value: str | int | float | bool | list[str] | None
    fact_id: str | None = None
    category: AnswerCategory
    basis: str = Field(description="fact, open_answer, or skip")
    note: str | None = None


class FormDraft(StrictModel):
    cover_letter: str
    answers: list[FormAnswer]
