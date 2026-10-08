"""Asynchronous Job Service with bounded queue backpressure and memory-bounded job store."""
from __future__ import annotations

import asyncio
import dataclasses as dc
import logging
import time
import uuid
from typing import Any, Literal

from ..config import Settings
from ..core.lru_cache import LRUCache
from .workers import WorkerRuntime

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "failed"]


class QueueFullError(Exception):
    """Raised when the bounded job queue is saturated."""


class ServiceUnavailableError(Exception):
    """Raised when the service is stopping and not accepting new jobs."""


@dc.dataclass
class JobRecord:
    job_id: str
    status: JobStatus
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    total_files: int = 0
    processed_files: int = 0
    result: dict | None = None
    error: str | None = None


class JobService:
    """Manages asynchronous job lifecycle, bounded queue backpressure, and consumer execution."""

    def __init__(
        self,
        worker_runtime: WorkerRuntime,
        queue_max_size: int = 50,
        job_retention_limit: int = 1_000,
    ) -> None:
        self.worker_runtime = worker_runtime
        self.queue_max_size = queue_max_size
        self._queue: asyncio.Queue[tuple[str, str, bool, Settings]] = asyncio.Queue(maxsize=queue_max_size)
        self._job_store: LRUCache[str, JobRecord] = LRUCache(
            max_items=job_retention_limit,
            ttl_s=86400.0,  # 24 hours TTL
            sizeof=lambda _: 1024,
        )
        self._accepting: bool = True
        self._consumer_task: asyncio.Task | None = None
        self._current_job_id: str | None = None

    def start_consumer(self) -> None:
        if self._consumer_task is None or self._consumer_task.done():
            self._consumer_task = asyncio.create_task(self._consumer_loop())

    async def submit_job(self, input_dir: str, use_github: bool, settings: Settings) -> str:
        if not self._accepting:
            raise ServiceUnavailableError("Service is shutting down; not accepting new jobs")

        if self._queue.full():
            raise QueueFullError("Job queue is full; retry later")

        job_id = str(uuid.uuid4())
        record = JobRecord(
            job_id=job_id,
            status="queued",
            created_at=time.time(),
        )
        self._job_store.put(job_id, record)
        try:
            self._queue.put_nowait((job_id, input_dir, use_github, settings))
        except asyncio.QueueFull:
            self._job_store.pop(job_id)
            raise QueueFullError("Job queue is full; retry later")

        return job_id

    def get_job(self, job_id: str) -> JobRecord | None:
        return self._job_store.get(job_id)

    def get_results(self, job_id: str) -> dict | None:
        record = self._job_store.get(job_id)
        if record is None or record.status != "done":
            return None
        return record.result

    async def _consumer_loop(self) -> None:
        while True:
            try:
                job_id, input_dir, use_github, settings = await self._queue.get()
            except asyncio.CancelledError:
                break

            record = self._job_store.get(job_id)
            if record is None:
                self._queue.task_done()
                continue

            record.status = "running"
            record.started_at = time.time()
            self._current_job_id = job_id

            def _on_progress(total: int, processed: int):
                record.total_files = total
                record.processed_files = processed

            try:
                result = await self.worker_runtime.execute_batch(
                    input_dir=input_dir,
                    settings=settings,
                    use_github=use_github,
                    on_progress=_on_progress,
                )
                record.status = "done"
                record.completed_at = time.time()
                record.result = result
            except Exception as e:
                log.exception("Job %s failed: %s", job_id, e)
                record.status = "failed"
                record.completed_at = time.time()
                record.error = str(e)
            finally:
                self._current_job_id = None
                self._queue.task_done()

    def stop_accepting(self) -> None:
        self._accepting = False

    async def drain(self, timeout_s: float = 15.0) -> None:
        """Wait for currently running job and enqueued jobs to drain up to timeout_s."""
        self.stop_accepting()
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout_s)
        except asyncio.TimeoutError:
            log.warning("Timed out while draining job queue")
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
