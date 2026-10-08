import asyncio
from pathlib import Path
import pytest

from screener.config import Settings
from screener.services.job_service import JobService, QueueFullError, ServiceUnavailableError
from screener.services.workers import WorkerRuntime
from helpers import DEEP, THIN


@pytest.fixture
def sample_resumes_dir(tmp_path):
    (tmp_path / "resume_1.txt").write_text(DEEP)
    (tmp_path / "resume_2.txt").write_text(THIN)
    return tmp_path


def test_job_service_lifecycle(sample_resumes_dir):
    runtime = WorkerRuntime(num_cpu_workers=2)

    async def run():
        service = JobService(worker_runtime=runtime, queue_max_size=5)
        service.start_consumer()

        # Submit job
        job_id = await service.submit_job(str(sample_resumes_dir), use_github=False, settings=Settings())
        assert job_id is not None

        # Poll status until done
        for _ in range(50):
            job = service.get_job(job_id)
            if job and job.status == "done":
                break
            await asyncio.sleep(0.1)

        job = service.get_job(job_id)
        assert job is not None
        assert job.status == "done"
        assert job.result is not None
        assert len(job.result["ranked_candidates"]) >= 1

        results = service.get_results(job_id)
        assert results is not None
        assert results["summary"]["total_files"] == 2
        assert service.latest_result is not None
        assert service.latest_result["summary"]["total_files"] == 2

        await service.drain()

    try:
        asyncio.run(run())
    finally:
        runtime.shutdown(wait=True)


def test_job_service_backpressure(sample_resumes_dir):
    runtime = WorkerRuntime(num_cpu_workers=1)

    async def run():
        # Queue max size = 1 (do not start consumer so queue fills)
        service = JobService(worker_runtime=runtime, queue_max_size=1)

        # 1st job fits in queue
        j1 = await service.submit_job(str(sample_resumes_dir), use_github=False, settings=Settings())
        assert j1 is not None

        # 2nd job must trigger QueueFullError
        with pytest.raises(QueueFullError):
            await service.submit_job(str(sample_resumes_dir), use_github=False, settings=Settings())

        # Test stop accepting
        service.stop_accepting()
        with pytest.raises(ServiceUnavailableError):
            await service.submit_job(str(sample_resumes_dir), use_github=False, settings=Settings())

    try:
        asyncio.run(run())
    finally:
        runtime.shutdown(wait=True)
