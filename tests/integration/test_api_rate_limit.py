import sys
from pathlib import Path
import time
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from screener.api.app import app
from helpers import DEEP


@pytest.fixture
def sample_dir(tmp_path):
    (tmp_path / "cand1.txt").write_text(DEEP)
    return tmp_path


def test_api_async_job_and_results_lifecycle(sample_dir):
    with TestClient(app) as client:
        # 1. Enqueue job via POST /screen (default async contract)
        resp = client.post(
            "/screen",
            json={"input_dir": str(sample_dir), "use_github": False},
            headers={"X-API-Key": "test-key-lifecycle"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "queued"
        job_id = body["job_id"]

        # 2. Check X-RateLimit headers are present
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers

        # 3. Poll /jobs/{job_id} until completed
        for _ in range(50):
            job_resp = client.get(f"/jobs/{job_id}", headers={"X-API-Key": "test-key-lifecycle"})
            assert job_resp.status_code == 200
            if job_resp.json()["status"] == "done":
                break
            time.sleep(0.1)

        job_data = client.get(f"/jobs/{job_id}", headers={"X-API-Key": "test-key-lifecycle"}).json()
        assert job_data["status"] == "done"

        # 4. Fetch results from /results/{job_id}
        res_resp = client.get(f"/results/{job_id}", headers={"X-API-Key": "test-key-lifecycle"})
        assert res_resp.status_code == 200
        result_data = res_resp.json()
        assert "ranked_candidates" in result_data
        assert len(result_data["ranked_candidates"]) == 1


def test_api_inbound_rate_limit_burst_429(sample_dir):
    with TestClient(app) as client:
        client_key = "flooding-client-123"
        # Write limit allows burst of 2 requests
        r1 = client.post(
            "/screen",
            json={"input_dir": str(sample_dir), "use_github": False},
            headers={"X-API-Key": client_key},
        )
        assert r1.status_code == 202

        r2 = client.post(
            "/screen",
            json={"input_dir": str(sample_dir), "use_github": False},
            headers={"X-API-Key": client_key},
        )
        assert r2.status_code == 202

        # 3rd request immediately exceeds burst capacity -> 429 Too Many Requests!
        r3 = client.post(
            "/screen",
            json={"input_dir": str(sample_dir), "use_github": False},
            headers={"X-API-Key": client_key},
        )
        assert r3.status_code == 429
        assert "Retry-After" in r3.headers
        assert "X-RateLimit-Limit" in r3.headers
        assert "X-RateLimit-Remaining" in r3.headers
        assert "X-RateLimit-Reset" in r3.headers
        assert int(r3.headers["Retry-After"]) >= 1


def test_api_metrics_and_health():
    with TestClient(app) as client:
        # Health check
        h_resp = client.get("/health")
        assert h_resp.status_code == 200
        h_data = h_resp.json()
        assert h_data["status"] == "ok"
        assert h_data["memory_rss_mb"] > 0
        assert "uptime_seconds" in h_data

        # Metrics check
        m_resp = client.get("/metrics")
        assert m_resp.status_code == 200
        m_data = m_resp.json()
        assert "entries" in m_data
        assert "bytes" in m_data
        assert "hits" in m_data
        assert "misses" in m_data
        assert "hit_ratio" in m_data
        assert "rate_limited_total" in m_data
        assert "rss_mb" in m_data
