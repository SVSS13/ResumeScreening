"""Focused integration and pipeline tests for the lean submission package."""
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

SUBMISSION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUBMISSION_ROOT / "src"))
sys.path.insert(0, str(SUBMISSION_ROOT))

from screener.config import Settings
from screener.pipeline import run
from screener.dedup import DuplicateDetector, BloomFilter
from screener.ingest import RawResume
from screener.parse import parse_resume
from api import app

SAMPLE_PYTHON_AI = """
John Doe | john@example.com
Summary: Software Engineer specializing in Python backend and AI agents.
Skills: Python, FastAPI, PostgreSQL, Docker, LangChain, RAG.
Experience:
- SDE Intern at TechCorp: Built an asynchronous RAG pipeline using Python and LangChain.
Projects:
- Multi-Agent Orchestrator: Autonomous agentic workflow with tool-calling and vector search.
"""

SAMPLE_JAVA_ONLY = """
Jane Smith | jane@example.com
Summary: Java backend engineer.
Skills: Java, Spring Boot, Hibernate, MySQL.
Experience: Built REST APIs in Java with Spring Boot.
"""


def test_bloom_filter_membership():
    bf = BloomFilter(capacity=100)
    bf.add("test_key")
    assert bf.might_contain("test_key")
    assert not bf.might_contain("absent_key")


def test_duplicate_detector():
    dd = DuplicateDetector()
    assert dd.check_and_add("r1.txt", SAMPLE_PYTHON_AI, "john@example.com") is None
    assert dd.check_and_add("r2.txt", SAMPLE_PYTHON_AI, "john@example.com") == "r1.txt"


def test_pipeline_on_synthetic_resumes(tmp_path):
    (tmp_path / "candidate_python.txt").write_text(SAMPLE_PYTHON_AI)
    (tmp_path / "candidate_java.txt").write_text(SAMPLE_JAVA_ONLY)

    settings = Settings()
    result = run(tmp_path, settings, use_github=False)

    assert result["summary"]["total_files"] == 2
    assert result["summary"]["eligible"] == 1
    assert result["summary"]["rejected"] == 1
    assert "John" in result["ranked_candidates"][0]["candidate_name"]
    assert not result["rejected_candidates"][0]["eligible"]


def test_submission_api_workflow(tmp_path):
    (tmp_path / "res_1.txt").write_text(SAMPLE_PYTHON_AI)
    client = TestClient(app)

    # Health check
    res_health = client.get("/health")
    assert res_health.status_code == 200

    # Screen endpoint
    res_screen = client.post("/screen", json={"input_dir": str(tmp_path), "use_github": False})
    assert res_screen.status_code == 200
    assert res_screen.json()["total_files"] == 1

    # Results endpoint
    res_results = client.get("/results")
    assert res_results.status_code == 200
    assert len(res_results.json()["ranked_candidates"]) == 1
