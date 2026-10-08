"""Minimal, zero-platform-dependency FastAPI application for the take-home submission."""
import sys
from pathlib import Path
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent / "src"))

from screener.config import Settings
from screener.pipeline import run

app = FastAPI(
    title="AI Resume Screening & Ranking Service (Lean Core)",
    version="1.0.0",
    description="Minimal FastAPI interface satisfying the take-home assignment brief.",
)

_last_result: dict = {}


class ScreenRequest(BaseModel):
    input_dir: str = Field(default="./resumes", description="Path to folder containing resumes")
    use_github: bool = Field(default=False, description="Whether to perform GitHub enrichment")


@app.post("/screen", summary="Run resume screening batch")
def screen(req: ScreenRequest) -> dict:
    target_path = Path(req.input_dir)
    if not target_path.exists() or not target_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Input directory not found: {req.input_dir}")

    settings = Settings.load()
    result = run(target_path, settings, use_github=req.use_github)
    _last_result["result"] = result
    return result["summary"]


@app.get("/results", summary="Retrieve final ranked results")
def get_results() -> dict:
    if "result" not in _last_result:
        raise HTTPException(status_code=404, detail="No batch has been run yet; POST /screen first")
    return _last_result["result"]


@app.get("/health", summary="Health check")
def health() -> dict:
    return {"status": "ok", "has_results": "result" in _last_result}
