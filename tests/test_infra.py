import asyncio
import json
from datetime import datetime, timezone

import httpx

from screener.config import GitHubConfig, LLMConfig, Settings
from screener.dedup import BloomFilter, DuplicateDetector
from screener.github_enrich import GitHubEnricher, score_repos
from screener.llm import LLMJudge, verify_evidence
from screener.models import LLMJudgement, LLMProject
from screener.pipeline import run
from helpers import DEEP, JS_ONLY, THIN

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


# ---------------- Bloom / dedup ----------------
def test_bloom_has_no_false_negatives_and_low_fp():
    bf = BloomFilter(capacity=2000, fp_rate=0.01)
    for i in range(2000):
        bf.add(f"item{i}")
    assert all(bf.might_contain(f"item{i}") for i in range(2000))
    fp = sum(bf.might_contain(f"other{i}") for i in range(5000)) / 5000
    assert fp < 0.03


def test_duplicate_detector_by_content_and_email():
    d = DuplicateDetector()
    assert d.check_and_add("a.txt", "Hello World resume", "a@x.com") is None
    assert d.check_and_add("b.txt", "hello   world   RESUME!!", None) == "a.txt"       # same content
    assert d.check_and_add("c.txt", "totally different", "A@x.com") == "a.txt"         # same email


# ---------------- GitHub ----------------
def _repo(name, days, **kw):
    ts = (NOW.timestamp() - days * 86400)
    return {"name": name, "fork": False, "archived": False, "language": "Python", "topics": [], "description": "",
            "pushed_at": datetime.fromtimestamp(ts, timezone.utc).isoformat(), **kw}


def test_github_scoring_recent_and_relevant():
    s = score_repos([_repo(f"r{i}", 10) for i in range(5)], NOW)
    assert s["activity"] == 5 and s["repo"] == 5


def test_github_scoring_stale_and_forks_ignored():
    s = score_repos([_repo("old", 900), _repo("fork", 1, fork=True)], NOW)
    assert s["activity"] == 0 and s["maintained"] == 0


def _enrich(handler, tmp_path, user="octo"):
    cfg = GitHubConfig(cache_path=str(tmp_path / "gh.json"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await GitHubEnricher(cfg, now=NOW).enrich(c, user)
    return asyncio.run(go())


def test_github_rate_limit_recorded_not_raised(tmp_path):
    r = _enrich(lambda req: httpx.Response(403, headers={"x-ratelimit-remaining": "0"}), tmp_path)
    assert r.status == "rate_limited" and r.score == 0


def test_github_404_and_network_error(tmp_path):
    assert _enrich(lambda req: httpx.Response(404), tmp_path).status == "not_found"

    def boom(req):
        raise httpx.ConnectError("down")
    assert _enrich(boom, tmp_path).status == "error"


def test_github_ok_path(tmp_path):
    body = [{k: v for k, v in _repo("agent-rag", 5).items()}]
    r = _enrich(lambda req: httpx.Response(200, json=body), tmp_path)
    assert r.status == "ok" and r.score > 0


# ---------------- LLM ----------------
def test_llm_hallucinated_evidence_is_dropped():
    j = LLMJudgement(overall_ai_depth=9, projects=[
        LLMProject(name="A", kind="agentic", depth=9, evidence="Built a stateful multi-agent LangGraph workflow"),
        LLMProject(name="B", kind="rag", depth=9, evidence="Invented a quantum retrieval engine for NASA")])
    kept = verify_evidence(j, DEEP).projects
    assert [p.name for p in kept] == ["A"]


class _Fake:
    def __init__(self, outputs): self.outputs = list(outputs)
    async def complete(self, system, user): return self.outputs.pop(0)


def test_llm_invalid_json_retries_then_gives_up(monkeypatch):
    async def _nosleep(*_):
        return None
    monkeypatch.setattr("screener.llm.asyncio.sleep", _nosleep)
    cfg = LLMConfig(provider="anthropic", max_retries=1)
    fake = _Fake(["not json", "{\"overall_ai_depth\": 99}"])                    # 2nd violates schema (<=10)
    assert asyncio.run(LLMJudge(fake, cfg).judge(DEEP)) is None
    assert fake.outputs == []                                                   # it retried exactly once


def test_llm_valid_output_parses():
    out = json.dumps({"overall_ai_depth": 8, "projects": [], "strengths": ["x"], "concerns": [], "summary": "s"})
    judge = LLMJudge(_Fake([f"```json\n{out}\n```"]), LLMConfig(provider="anthropic"))
    assert asyncio.run(judge.judge(DEEP)).overall_ai_depth == 8


# ---------------- End-to-end resilience ----------------
def test_batch_survives_bad_files_and_duplicates(tmp_path):
    (tmp_path / "good.txt").write_text(DEEP)
    (tmp_path / "dup.txt").write_text(DEEP)
    (tmp_path / "thin.txt").write_text(THIN)
    (tmp_path / "js.txt").write_text(JS_ONLY)
    (tmp_path / "corrupt.pdf").write_bytes(b"%PDF-1.4 this is not a pdf")
    (tmp_path / "empty.txt").write_text("")
    res = run(tmp_path, Settings(), use_github=False)
    s = res["summary"]
    assert s["total_files"] == 6 and s["eligible"] == 2 and s["rejected"] == 1
    assert s["duplicates_skipped"] == 1 and s["failed_unreadable"] == 2
    assert res["ranked_candidates"][0]["candidate_name"] == "Asha Rao"
    assert res["ranked_candidates"][0]["rank"] == 1
    assert res["rejected_candidates"][0]["rejection_reasons"]


def test_api_screen_and_results(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from api import app
    (tmp_path / "a.txt").write_text(DEEP)
    c = TestClient(app)
    assert c.get("/results").status_code == 404
    r = c.post("/screen", json={"input_dir": str(tmp_path), "use_github": False})
    assert r.status_code == 200 and r.json()["eligible"] == 1
    assert c.get("/results").json()["ranked_candidates"][0]["rank"] == 1
    assert c.post("/screen", json={"input_dir": "/nope"}).status_code == 404
