"""Lightweight GitHub enrichment: 1 API call per unique user, bounded concurrency, disk + in-run cache.

Score (max 10): activity 0-5 (recency of latest push to an own repo) + repos 0-5
(maintained repos 0-3, Python/AI-relevant maintained repos 0-2). Any failure => status recorded, batch continues.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .config import GitHubConfig
from .models import GitHubResult

log = logging.getLogger(__name__)
API = "https://api.github.com"
AI_REPO_RX = re.compile(r"llm|rag|agent|gpt|langchain|langgraph|embedding|vector|openai|gemini|nlp|"
                        r"transformer|chatbot|mcp|retriev|ml\b|machine.?learning|deep.?learning", re.I)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def score_repos(repos: list[dict], now: datetime) -> dict:
    """Pure function (unit-tested). `repos` = slim dicts from the GitHub repos endpoint."""
    own = [r for r in repos if not r.get("fork")]
    if not own:
        return {"activity": 0.0, "repo": 0.0, "maintained": 0, "relevant": 0, "last_push": None}
    last = max(_parse(r["pushed_at"]) for r in own if r.get("pushed_at"))
    days = (now - last).days
    activity = 5.0 if days <= 30 else 4.0 if days <= 90 else 3.0 if days <= 180 else 1.5 if days <= 365 else 0.0
    cutoff = now - timedelta(days=365)
    maintained = [r for r in own if not r.get("archived") and r.get("pushed_at") and _parse(r["pushed_at"]) >= cutoff]
    n = len(maintained)
    m_pts = 3.0 if n >= 5 else 2.0 if n >= 3 else 1.0 if n >= 1 else 0.0
    relevant = [r for r in maintained if r.get("language") == "Python" or AI_REPO_RX.search(
        " ".join([r.get("name") or "", r.get("description") or "", " ".join(r.get("topics") or [])]))]
    r_pts = 2.0 if len(relevant) >= 2 else 1.0 if relevant else 0.0
    return {"activity": activity, "repo": min(5.0, m_pts + r_pts), "maintained": n,
            "relevant": len(relevant), "last_push": last.date().isoformat()}


class GitHubEnricher:
    def __init__(self, cfg: GitHubConfig, now: datetime | None = None):
        self.cfg = cfg
        self.now = now or datetime.now(timezone.utc)
        self._sem = asyncio.Semaphore(cfg.max_concurrency)
        self._inflight: dict[str, asyncio.Task] = {}
        self._rate_limited = False
        self._cache_path = Path(cfg.cache_path)
        self._cache = self._load_cache()
        token = os.getenv(cfg.token_env)           # token only ever comes from the environment
        self._headers = {"Accept": "application/vnd.github+json", "User-Agent": "resume-screener"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    # ---- cache -----------------------------------------------------------
    def _load_cache(self) -> dict:
        try:
            return json.loads(self._cache_path.read_text())
        except Exception:
            return {}

    def save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache))
        except Exception as e:
            log.warning("could not persist github cache: %s", e)

    def _cached(self, user: str) -> list[dict] | None:
        e = self._cache.get(user.lower())
        if e and datetime.fromisoformat(e["at"]) > self.now - timedelta(hours=self.cfg.cache_ttl_hours):
            return e["repos"]
        return None

    # ---- public API --------------------------------------------------------
    async def enrich(self, client: httpx.AsyncClient, user: str | None) -> GitHubResult:
        if not user:
            return GitHubResult(status="no_profile", summary="No GitHub profile found in resume",
                                score=self.cfg.missing_score)
        key = user.lower()
        if key not in self._inflight:                      # de-duplicate concurrent requests per user
            self._inflight[key] = asyncio.ensure_future(self._enrich_one(client, user))
        return await self._inflight[key]

    async def _enrich_one(self, client: httpx.AsyncClient, user: str) -> GitHubResult:
        base = GitHubResult(username=user, profile_url=f"https://github.com/{user}", score=self.cfg.missing_score)
        repos = self._cached(user)
        if repos is None:
            if self._rate_limited:
                return base.model_copy(update={"status": "rate_limited", "summary": "GitHub rate limit hit earlier in run"})
            try:
                repos = await self._fetch_repos(client, user)
            except _NotFound:
                return base.model_copy(update={"status": "not_found", "summary": "GitHub user not found / private"})
            except _RateLimited as e:
                self._rate_limited = True
                return base.model_copy(update={"status": "rate_limited", "detail": str(e),
                                               "summary": "GitHub API rate-limited; set GITHUB_TOKEN to raise limit"})
            except Exception as e:                           # network, JSON, anything
                return base.model_copy(update={"status": "error", "detail": f"{type(e).__name__}: {e}",
                                               "summary": "GitHub enrichment failed"})
            self._cache[user.lower()] = {"at": self.now.isoformat(), "repos": repos}
        s = score_repos(repos, self.now)
        total = min(10.0, s["activity"] + s["repo"])
        summary = (f"Last push {s['last_push']}; {s['maintained']} maintained repos "
                   f"({s['relevant']} Python/AI-relevant)") if s["last_push"] else "No original public repositories"
        return base.model_copy(update={"status": "ok", "activity_score": s["activity"], "repo_score": s["repo"],
                                       "score": total, "summary": summary, "maintained_repos": s["maintained"],
                                       "relevant_repos": s["relevant"], "last_push": s["last_push"]})

    async def _fetch_repos(self, client: httpx.AsyncClient, user: str) -> list[dict]:
        async with self._sem:
            r = await client.get(f"{API}/users/{user}/repos", params={"per_page": 100, "sort": "pushed", "type": "owner"},
                                 headers=self._headers, timeout=self.cfg.timeout_s)
        if r.status_code == 404:
            raise _NotFound()
        if r.status_code == 429 or (r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
            raise _RateLimited(f"HTTP {r.status_code}, resets at {r.headers.get('x-ratelimit-reset')}")
        r.raise_for_status()
        keep = ("name", "fork", "archived", "pushed_at", "language", "topics", "description", "stargazers_count")
        return [{k: repo.get(k) for k in keep} for repo in r.json()]


class _NotFound(Exception): ...
class _RateLimited(Exception): ...
