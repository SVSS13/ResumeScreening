"""Dependency injection container and accessors for services."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.memory_guard import MemoryGuard
from ..core.metrics import MetricsCollector
from ..services.job_service import JobService
from ..services.workers import WorkerRuntime


class ServiceContainer:
    """Holds shared lifecycle dependencies for FastAPI routes."""

    def __init__(self) -> None:
        self._worker_runtime: WorkerRuntime | None = None
        self._job_service: JobService | None = None
        self._metrics_collector: MetricsCollector | None = None
        self._memory_guard: MemoryGuard | None = None

    @property
    def worker_runtime(self) -> WorkerRuntime:
        if self._worker_runtime is None:
            self._worker_runtime = WorkerRuntime()
        return self._worker_runtime

    @worker_runtime.setter
    def worker_runtime(self, val: WorkerRuntime) -> None:
        self._worker_runtime = val

    @property
    def job_service(self) -> JobService:
        if self._job_service is None:
            self._job_service = JobService(worker_runtime=self.worker_runtime, queue_max_size=50)
            self._job_service.start_consumer()
        return self._job_service

    @job_service.setter
    def job_service(self, val: JobService) -> None:
        self._job_service = val

    @property
    def metrics_collector(self) -> MetricsCollector:
        if self._metrics_collector is None:
            self._metrics_collector = MetricsCollector()
        return self._metrics_collector

    @metrics_collector.setter
    def metrics_collector(self, val: MetricsCollector) -> None:
        self._metrics_collector = val

    @property
    def memory_guard(self) -> MemoryGuard:
        if self._memory_guard is None:
            self._memory_guard = MemoryGuard()
        return self._memory_guard

    @memory_guard.setter
    def memory_guard(self, val: MemoryGuard) -> None:
        self._memory_guard = val


container = ServiceContainer()


def get_job_service() -> JobService:
    return container.job_service


def get_metrics_collector() -> MetricsCollector:
    return container.metrics_collector


def get_memory_guard() -> MemoryGuard:
    return container.memory_guard
