"""Pydantic request and response schemas for FastAPI endpoints."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class ScreenRequest(BaseModel):
    input_dir: str = Field(default="./resumes", description="Path to folder containing resumes")
    use_github: bool = Field(default=True, description="Whether to perform GitHub enrichment")
    sync: bool = Field(default=False, description="Run synchronously and return batch summary")


class ScreenResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "failed"]
    message: str = "Job accepted for processing"


class JobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "failed"]
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    total_files: int = 0
    processed_files: int = 0
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="Server health status")
    uptime_seconds: float = Field(description="Server uptime in seconds")
    memory_rss_mb: float = Field(description="Current resident set size memory in MB")
    jobs_accepting: bool = Field(description="Whether the job queue is accepting new submissions")


class ScoreBreakdownSchema(BaseModel):
    ai_project_depth: float = Field(description="Score for AI project depth (0-30)")
    python_backend: float = Field(description="Score for Python backend engineering (0-25)")
    cloud_fullstack: float = Field(description="Score for Cloud & Fullstack proficiency (0-15)")
    github: float = Field(description="Score for GitHub activity and repositories (0-10)")
    engineering_depth: float = Field(description="Score for general engineering depth (0-20)")


class RankedCandidateSchema(BaseModel):
    rank: int = Field(description="Candidate rank (1-indexed)")
    candidate_name: str = Field(description="Extracted candidate name")
    file: str = Field(description="Filename of the resume")
    eligible: bool = Field(default=True, description="Hard eligibility pass flag")
    total_score: float = Field(description="Total candidate score (0-100)")
    score_breakdown: ScoreBreakdownSchema = Field(description="Category score breakdown")
    penalties: list[dict] = Field(default_factory=list, description="Audit deductions applied")
    github: Optional[dict] = Field(default=None, description="GitHub enrichment telemetry")
    matched_skills: list[str] = Field(default_factory=list, description="Key skills identified")


class RejectedCandidateSchema(BaseModel):
    candidate_name: str = Field(description="Candidate name or filename")
    file: str = Field(description="Filename of the resume")
    eligible: bool = Field(default=False, description="Eligibility status")
    total_score: Optional[float] = Field(default=None, description="Preliminary score if evaluated")
    rejection_reasons: list[str] = Field(default_factory=list, description="Explicit reasons for disqualification")


class BatchSummarySchema(BaseModel):
    total_files: int = Field(description="Total resume files processed")
    parsed_ok: int = Field(description="Files parsed successfully")
    eligible: int = Field(description="Total eligible candidates")
    rejected: int = Field(description="Total rejected candidates")
    failed_unreadable: int = Field(description="Files unreadable or corrupted")
    duplicates_skipped: int = Field(description="Duplicate resumes skipped")
    runtime_seconds: float = Field(description="Total batch processing elapsed time")
    github_ok: int = Field(description="GitHub profiles enriched successfully")
    github_failed: int = Field(description="GitHub lookups that failed or were rate-limited")
    llm_ok: int = Field(default=0, description="LLM advisory judgements completed")
    llm_failed: int = Field(default=0, description="LLM calls failed")


class RankingResultsResponse(BaseModel):
    summary: BatchSummarySchema = Field(description="Aggregated batch execution statistics")
    ranked_candidates: list[RankedCandidateSchema] = Field(description="Ranked list of qualified candidates")
    rejected_candidates: list[RejectedCandidateSchema] = Field(description="List of disqualified candidates with audit reasons")
    failed_files: list[dict] = Field(default_factory=list, description="Files that could not be parsed or processed")
