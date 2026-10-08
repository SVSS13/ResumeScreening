"""FastAPI application with lifespan lifecycle, job service, and rate limiting."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import time
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import JSONResponse

from ..config import Settings
from ..core.memory_guard import MemoryGuard, get_process_rss_mb
from ..core.metrics import MetricsCollector
from ..services.job_service import JobService, QueueFullError, ServiceUnavailableError
from ..services.workers import WorkerRuntime
from .deps import container, get_job_service, get_metrics_collector
from .middleware import RateLimitMiddleware
from .schemas import HealthResponse, JobResponse, ScreenRequest, ScreenResponse, RankingResultsResponse

import logging
from ..pipeline import run

logging.getLogger("pdfminer").setLevel(logging.ERROR)

_start_time = time.time()
_last_result: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    worker_runtime = WorkerRuntime()
    job_service = JobService(worker_runtime=worker_runtime, queue_max_size=50)
    job_service.start_consumer()

    metrics_collector = MetricsCollector()
    memory_guard = MemoryGuard()
    memory_guard.start()

    container.worker_runtime = worker_runtime
    container.job_service = job_service
    container.metrics_collector = metrics_collector
    container.memory_guard = memory_guard

    yield

    await job_service.drain(timeout_s=15.0)
    memory_guard.stop()
    worker_runtime.shutdown(wait=True)


API_DESCRIPTION = """
## AI Resume Screening & Ranking Platform (API Reference)

### End-to-End Workflow:
1. **Submit Batch Job (`POST /screen`)**:
   - Provide `input_dir` (e.g. `./resumes`), `use_github` boolean, and optional `sync: false`.
   - Returns HTTP `202 Accepted` with a unique `job_id` (e.g. `53588fa7-07e4-48ca-ab36-f2dd69f05193`).
2. **Monitor Job Progress (`GET /jobs/{job_id}`)**:
   - Poll until `status` transitions from `"queued"` -> `"running"` -> `"done"`.
   - Inspect file progress: `processed_files / total_files`.
3. **Retrieve Rankings (`GET /results/{job_id}`)**:
   - Returns fully ranked qualified candidates, deterministic 100-point score breakdowns, penalties, and audit reasons for rejected candidates.
   - Or call parameterless `GET /results` to automatically fetch the most recently completed screening run!
4. **Telemetry & Health**:
   - `GET /metrics`: In-memory cache hit ratio, eviction counts, rate limits, and memory RSS.
   - `GET /health`: Liveness and readiness probe with process memory RSS.

*Tip:* When providing `job_id`, use the raw UUID (e.g. `53588fa7-07e4-48ca-ab36-f2dd69f05193`). Do NOT include `jobs/`.
"""

app = FastAPI(
    title="AI Resume Screening & Ranking Service",
    version="2.0.0",
    description=API_DESCRIPTION,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Screening Jobs", "description": "Enqueue and poll background screening batch jobs"},
        {"name": "Rankings & Results", "description": "Retrieve ranked candidate lists and score breakdowns"},
        {"name": "Observability", "description": "System health, memory telemetry, and cache metrics"},
    ],
)
metrics_collector_global = MetricsCollector()
app.add_middleware(RateLimitMiddleware, metrics=metrics_collector_global)


@app.post(
    "/screen",
    response_model=ScreenResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a resume screening batch job",
    tags=["Screening Jobs"],
    description="Enqueue a folder of PDF/DOCX/TXT resumes for asynchronous screening. Returns a unique job_id.",
)
async def screen(
    req: ScreenRequest,
    job_service: JobService = Depends(get_job_service),
):
    target_path = Path(req.input_dir)
    if not target_path.exists() or not target_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Input directory does not exist: {req.input_dir}",
        )

    # Synchronous mode for CLI/direct caller parity (runs on worker_runtime without nested asyncio.run)
    if req.sync:
        result = await job_service.worker_runtime.execute_batch(
            input_dir=str(target_path),
            settings=Settings.load(),
            use_github=req.use_github,
        )
        _last_result["result"] = result
        return JSONResponse(status_code=status.HTTP_200_OK, content=result["summary"])

    try:
        job_id = await job_service.submit_job(
            input_dir=str(target_path),
            use_github=req.use_github,
            settings=Settings.load(),
        )
        return ScreenResponse(job_id=job_id, status="queued")
    except QueueFullError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Job queue full; backpressure active"},
            headers={"Retry-After": "30"},
        )
    except ServiceUnavailableError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Service is draining and shutting down"},
            headers={"Retry-After": "15"},
        )


@app.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Retrieve status and progress of a screening job",
    tags=["Screening Jobs"],
    description="Check the current status and file parsing progress of a job by its UUID.",
)
async def get_job_status(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
) -> JobResponse:
    clean_id = job_id.removeprefix("jobs/").removeprefix("jobs%2F").strip()
    job = job_service.get_job(clean_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job not found: {clean_id}")

    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        total_files=job.total_files,
        processed_files=job.processed_files,
        error=job.error,
    )


@app.get(
    "/results",
    response_model=RankingResultsResponse,
    summary="Retrieve results of the last completed run",
    tags=["Rankings & Results"],
    description="Convenience endpoint returning the final ranking results from the most recent completed screening run.",
)
async def get_latest_results(
    job_service: JobService = Depends(get_job_service),
) -> dict:
    if "result" in _last_result:
        return _last_result["result"]

    if job_service.latest_result is not None:
        return job_service.latest_result

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No run yet; POST /screen first")


@app.get(
    "/results/{job_id}",
    response_model=RankingResultsResponse,
    summary="Retrieve final ranking results for a completed job",
    tags=["Rankings & Results"],
    description="Fetch the complete ranked candidate list, scores, and rejection audit trail for a finished job_id.",
)
async def get_job_results(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
) -> dict:
    clean_id = job_id.removeprefix("jobs/").removeprefix("jobs%2F").strip()
    job = job_service.get_job(clean_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job not found: {clean_id}")

    if job.status == "failed":
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=job.error or "Job failed")

    if job.status != "done":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is still {job.status}; check /jobs/{clean_id} for progress",
        )

    results = job_service.get_results(clean_id)
    if results is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Results not available")

    return results


@app.get(
    "/metrics",
    summary="Observability metrics for cache hits, memory RSS, and rate limits",
    tags=["Observability"],
    description="Real-time in-memory telemetry snapshot including cache hits/misses, RSS MB, and rate limit counters.",
)
async def metrics(
    collector: MetricsCollector = Depends(get_metrics_collector),
) -> dict:
    return collector.snapshot()


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and readiness health check",
    tags=["Observability"],
    description="Health probe returning uptime, current RSS memory in MB, and queue availability status.",
)
async def health(
    job_service: JobService = Depends(get_job_service),
) -> HealthResponse:
    return HealthResponse(
        status="ok",
        uptime_seconds=round(time.time() - _start_time, 2),
        memory_rss_mb=round(get_process_rss_mb(), 2),
        jobs_accepting=job_service._accepting,
    )
