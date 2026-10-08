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
    status: str = "ok"
    uptime_seconds: float
    memory_rss_mb: float
    jobs_accepting: bool
