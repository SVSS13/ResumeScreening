"""Hardened unit tests covering findings A1, A4, A6, and A10.

Verifies:
- Portable MemoryGuard RSS parsing across simulated Linux, Darwin, and Windows platforms.
- OutboundLimiter dynamic epoch reset delta calculation and adaptive pausing.
- CacheAdapter coverage (ParseCacheAdapter, GitHubCacheAdapter, LLMCacheAdapter).
- OutboundClient coverage (RateLimitedAsyncClient).
- Report generation coverage (write_json, write_csv, terminal_report).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch
import pytest

from screener.core.memory_guard import get_process_rss_mb, MemoryGuard
from screener.core.rate_limiter import OutboundLimiter
from screener.adapters.cache_adapter import ParseCacheAdapter, GitHubCacheAdapter, LLMCacheAdapter
from screener.adapters.outbound_client import RateLimitedAsyncClient
from screener.ingest import RawResume
from screener.models import LLMJudgement
from screener.report import write_json, write_csv, terminal_report


# ============================================================================
# Finding A4: Cross-Platform Memory Telemetry Tests
# ============================================================================

def test_rss_linux_proc_statm():
    """Verify Linux /proc/self/statm reading."""
    mock_statm = "1000 256 128 64 0 128 0\n"  # 256 pages
    with patch("sys.platform", "linux"), \
         patch("screener.core.memory_guard.psutil", None), \
         patch("builtins.open", mock_open(read_data=mock_statm)), \
         patch("os.sysconf", return_value=4096):
        rss = get_process_rss_mb()
        # 256 * 4096 / 1024 / 1024 = 1.0 MB
        assert rss == pytest.approx(1.0, abs=1e-2)


def test_rss_darwin_macos_bytes_division():
    """Verify macOS Darwin converts bytes directly to MB (dividing by 1024^2)."""
    mock_resource = MagicMock()
    # On macOS, ru_maxrss is in bytes: 104,857,600 bytes = 100 MB
    mock_resource.getrusage.return_value.ru_maxrss = 104857600
    mock_resource.RUSAGE_SELF = 0

    with patch("sys.platform", "darwin"), \
         patch("screener.core.memory_guard.psutil", None), \
         patch("screener.core.memory_guard.resource", mock_resource):
        rss = get_process_rss_mb()
        assert rss == pytest.approx(100.0, abs=1e-2)


def test_rss_windows_fallback_safe():
    """Verify Windows / fallback returns 0.0 without throwing exceptions."""
    with patch("sys.platform", "win32"), \
         patch("screener.core.memory_guard.psutil", None), \
         patch("screener.core.memory_guard.resource", None):
        rss = get_process_rss_mb()
        assert rss == 0.0


# ============================================================================
# Finding A10: Upstream Rate Limit Epoch Reset Math
# ============================================================================

def test_outbound_limiter_epoch_reset():
    """Verify OutboundLimiter calculates exact epoch deltas on x-ratelimit-reset."""
    async def run_test():
        limiter = OutboundLimiter(max_concurrency=2, rate_per_s=10.0)
        now_epoch = time.time()
        future_reset = now_epoch + 1.5  # resets in 1.5 seconds

        headers = {
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": str(future_reset),
        }
        await limiter.observe(headers, status_code=403)

        # paused_until should be roughly now + 1.5s (+0.5s safety buffer)
        async with limiter._lock:
            paused_s = limiter._paused_until - limiter._clock()
        assert 1.4 <= paused_s <= 2.5

    asyncio.run(run_test())


# ============================================================================
# Finding A6: Adapters Coverage
# ============================================================================

def test_parse_cache_adapter(tmp_path):
    adapter = ParseCacheAdapter()
    raw = RawResume(
        path=tmp_path / "resume.txt",
        text="Experienced Python and FastAPI backend developer with Docker.",
    )
    p1 = adapter.parse(raw)
    assert p1 is not None
    # Hit cache
    p2 = adapter.parse(raw)
    assert p1 is p2 or p1.text == p2.text


def test_github_cache_adapter(tmp_path):
    cache_file = tmp_path / "gh_cache.json"
    adapter = GitHubCacheAdapter(cache_path=str(cache_file))
    
    assert adapter.get("octocat") is None
    sample_repos = [{"name": "repo1", "fork": False, "pushed_at": "2026-01-01T00:00:00Z"}]
    adapter.put("octocat", sample_repos)
    
    assert adapter.get("octocat") == sample_repos
    assert adapter._bloom.might_contain("octocat")
    
    # Save snapshot atomically
    adapter.save_snapshot()
    assert cache_file.exists()
    
    # Reload from disk
    new_adapter = GitHubCacheAdapter(cache_path=str(cache_file))
    assert new_adapter.get("octocat") == sample_repos


def test_llm_cache_adapter():
    adapter = LLMCacheAdapter()
    assert adapter.get("prompt_hash_1") is None
    j = LLMJudgement(overall_ai_depth=8, summary="Strong fit")
    adapter.put("prompt_hash_1", j)
    assert adapter.get("prompt_hash_1") == j


def test_outbound_client_adapter():
    async def run():
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        
        mock_http = MagicMock()
        mock_http.get = MagicMock(return_value=asyncio.sleep(0.001, result=mock_response))
        mock_http.post = MagicMock(return_value=asyncio.sleep(0.001, result=mock_response))
        
        limiter = OutboundLimiter(max_concurrency=2, rate_per_s=10.0)
        client = RateLimitedAsyncClient(mock_http, limiter)
        
        res1 = await client.get("https://api.github.com/test")
        assert res1.status_code == 200
        res2 = await client.post("https://api.github.com/test", json={"k": "v"})
        assert res2.status_code == 200

    asyncio.run(run())


# ============================================================================
# Finding A6: Report Coverage
# ============================================================================

def test_report_writers(tmp_path):
    sample_result = {
        "summary": {
            "total_files": 2,
            "parsed_ok": 2,
            "eligible": 1,
            "rejected": 1,
            "failed_unreadable": 0,
            "duplicates_skipped": 0,
            "runtime_seconds": 0.5,
            "github_ok": 1,
            "github_failed": 0,
            "llm_ok": 0,
            "llm_failed": 0,
        },
        "ranked_candidates": [
            {
                "rank": 1,
                "candidate_name": "Alice",
                "file": "alice.pdf",
                "eligible": True,
                "total_score": 88.5,
                "score_breakdown": {
                    "ai_project_depth": 25.0,
                    "python_backend": 25.0,
                    "cloud_fullstack": 15.0,
                    "github": 8.5,
                    "engineering_depth": 15.0,
                },
                "penalties": [],
                "github": {"status": "ok"},
                "matched_skills": ["Python", "Docker"],
            }
        ],
        "rejected_candidates": [
            {
                "candidate_name": "Bob",
                "file": "bob.pdf",
                "eligible": False,
                "total_score": 20.0,
                "score_breakdown": {},
                "penalties": [],
                "rejection_reasons": ["Graduation year cutoff breached"],
            }
        ],
        "failed_files": [],
    }

    # 1. JSON
    json_path = tmp_path / "out.json"
    write_json(sample_result, json_path)
    assert json_path.exists()
    loaded = json.loads(json_path.read_text())
    assert loaded["summary"]["total_files"] == 2

    # 2. CSV
    csv_path = tmp_path / "out.csv"
    write_csv(sample_result, csv_path)
    assert csv_path.exists()
    assert "Alice" in csv_path.read_text()
    assert "Bob" in csv_path.read_text()

    # 3. Terminal Report
    rep = terminal_report(sample_result, top=5)
    assert "Alice" in rep
    assert "Batch: 2 files" in rep
