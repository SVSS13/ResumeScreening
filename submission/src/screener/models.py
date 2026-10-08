"""Typed contracts shared across modules (Pydantic v2)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ScoreBreakdown(BaseModel):
    ai_project_depth: float = 0.0
    python_backend: float = 0.0
    cloud_fullstack: float = 0.0
    github: float = 0.0
    engineering_depth: float = 0.0


class Penalty(BaseModel):
    reason: str
    points: float


class GitHubResult(BaseModel):
    username: Optional[str] = None
    profile_url: Optional[str] = None
    status: Literal["ok", "no_profile", "not_found", "rate_limited", "error", "disabled"] = "no_profile"
    activity_score: float = 0.0
    repo_score: float = 0.0
    score: float = 0.0
    summary: str = ""
    maintained_repos: int = 0
    relevant_repos: int = 0
    last_push: Optional[str] = None
    detail: Optional[str] = None


# ---- LLM structured output -------------------------------------------------
class LLMProject(BaseModel):
    name: str
    kind: Literal["agentic", "rag", "llm_wrapper", "classical_ml", "tutorial", "non_ai"]
    depth: int = Field(ge=0, le=10)
    evidence: str = Field(description="Short verbatim quote from the resume supporting the judgement")


class LLMJudgement(BaseModel):
    projects: list[LLMProject] = []
    overall_ai_depth: int = Field(ge=0, le=10)
    strengths: list[str] = []
    concerns: list[str] = []
    summary: str = ""


# ---- Final output ----------------------------------------------------------
class CandidateResult(BaseModel):
    rank: Optional[int] = None
    candidate_name: str
    file: str
    email: Optional[str] = None
    eligible: bool
    total_score: Optional[float] = None
    score_breakdown: Optional[ScoreBreakdown] = None
    penalties: list[Penalty] = []
    matched_skills: list[str] = []
    rejection_reasons: list[str] = []
    project_summary: str = ""
    evidence: list[str] = []
    github: Optional[GitHubResult] = None
    github_summary: str = ""
    strengths: list[str] = []
    concerns: list[str] = []
    jd_similarity: Optional[float] = None
    llm_status: str = "disabled"
    warnings: list[str] = []


class FailedFile(BaseModel):
    file: str
    reason: str


class BatchSummary(BaseModel):
    total_files: int
    parsed_ok: int
    duplicates_skipped: int
    failed_unreadable: int
    eligible: int
    rejected: int
    github_ok: int = 0
    github_failed: int = 0
    llm_ok: int = 0
    llm_failed: int = 0
    runtime_seconds: float = 0.0
