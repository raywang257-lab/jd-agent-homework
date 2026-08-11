from __future__ import annotations

from pydantic import BaseModel, Field


class JobInput(BaseModel):
    job_title: str = ""
    department: str = ""
    location: str = ""
    work_mode: str = "现场办公"
    seniority: str = ""
    experience: str = ""
    education: str = ""
    salary: str = ""
    job_goal: str = ""
    responsibilities: str = ""
    required_skills: str = ""
    preferred_skills: str = ""
    selling_points: str = ""
    platform: str = "BOSS直聘"


class JDContent(BaseModel):
    job_title: str
    job_summary: str
    responsibilities: list[str] = Field(min_length=1)
    requirements: list[str] = Field(min_length=1)
    preferred_qualifications: list[str] = []
    selling_points: list[str] = []
    location_and_mode: str
    salary_and_benefits: str = "面议"


class RiskIssue(BaseModel):
    level: str
    text: str
    reason: str
    suggestion: str
    category: str = "其他"


class RiskAssessment(BaseModel):
    overall_level: str
    score: int = Field(ge=0, le=100)
    issues: list[RiskIssue] = Field(default_factory=list)


class FieldIssue(BaseModel):
    field: str
    label: str
    message: str
    question: str
