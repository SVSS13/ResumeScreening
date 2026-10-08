"""GIL-aware worker runtime partitioning CPU, I/O, and disk concurrency.

OS Concepts:
- CPU-bound (PDF extraction, text parsing, TF-IDF vectorization): ProcessPoolExecutor bypasses the GIL.
- I/O-bound (GitHub & LLM HTTP requests): asyncio event loop with httpx.AsyncClient.
- Disk I/O (snapshots, cache saves): Bounded ThreadPoolExecutor.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import logging
import os
from pathlib import Path
import time
from typing import Any

import httpx

from ..config import Settings
from ..core.rate_limiter import OutboundLimiter
from ..dedup import DuplicateDetector
from ..eligibility import Eligibility, check_eligibility
from ..features import scan
from ..github_enrich import GitHubEnricher
from ..ingest import RawResume, discover, load_resume
from ..llm import LLMJudge, make_client
from ..models import BatchSummary, CandidateResult, FailedFile, GitHubResult, LLMJudgement
from ..parse import ParsedResume, parse_resume
from ..scoring import DetScore, blend_llm, score_candidate, total_score
from ..tfidf import SemanticScorer

log = logging.getLogger(__name__)


def _cpu_load_and_parse(path: Path) -> tuple[Path, RawResume, ParsedResume | None, str | None]:
    """Top-level worker function executed inside ProcessPoolExecutor."""
    try:
        raw = load_resume(path)
        if raw.error:
            return path, raw, None, raw.error
        parsed = parse_resume(raw)
        return path, raw, parsed, None
    except Exception as e:
        return path, RawResume(path, error=str(e)), None, f"parse error: {type(e).__name__}: {e}"


class WorkerRuntime:
    """Coordinates execution across ProcessPool (CPU), asyncio (I/O), and ThreadPool (disk)."""

    def __init__(
        self,
        num_cpu_workers: int | None = None,
        num_io_threads: int = 4,
    ) -> None:
        self.num_cpu_workers = num_cpu_workers or min(os.cpu_count() or 4, 8)
        self.num_io_threads = num_io_threads
        import multiprocessing
        mp_ctx = multiprocessing.get_context("fork") if "fork" in multiprocessing.get_all_start_methods() else None
        self._process_pool = ProcessPoolExecutor(max_workers=self.num_cpu_workers, mp_context=mp_ctx)
        self._thread_pool = ThreadPoolExecutor(max_workers=self.num_io_threads)
        self._outbound_limiter = OutboundLimiter(max_concurrency=5, rate_per_s=2.0)

    def shutdown(self, wait: bool = True) -> None:
        self._process_pool.shutdown(wait=wait)
        self._thread_pool.shutdown(wait=wait)

    async def execute_batch(
        self,
        input_dir: str | Path,
        settings: Settings,
        use_github: bool = True,
        on_progress: Any = None,
    ) -> dict:
        """Fully asynchronous pipeline execution without nested asyncio.run()."""
        t0 = time.perf_counter()
        paths = discover(input_dir)
        total_files = len(paths)
        if on_progress:
            on_progress(total_files, 0)

        loop = asyncio.get_running_loop()

        # ---- 1. Ingest & Parse in ProcessPoolExecutor (CPU-bound) ----
        # Dispatch file parsing across process pool
        parse_futures = [
            loop.run_in_executor(self._process_pool, _cpu_load_and_parse, p)
            for p in paths
        ]
        parsed_results = await asyncio.gather(*parse_futures)

        failed: list[FailedFile] = []
        duplicates: list[dict] = []
        dedup = DuplicateDetector(settings.bloom_capacity, settings.bloom_fp_rate)
        parsed_all: list[ParsedResume] = []

        for path, raw, parsed, err in parsed_results:
            if err or raw.error:
                failed.append(FailedFile(file=path.name, reason=err or raw.error or "unknown"))
                continue
            assert parsed is not None
            dup_of = dedup.check_and_add(path.name, raw.text, parsed.email)
            if dup_of:
                duplicates.append({"file": path.name, "duplicate_of": dup_of})
                continue
            parsed_all.append(parsed)

        # ---- 2. Hard Eligibility & Feature Scan (CPU-bound) ----
        analysed: list[tuple[ParsedResume, dict, Eligibility]] = []
        for p in parsed_all:
            try:
                hits = scan(p)
                analysed.append((p, hits, check_eligibility(p, hits, settings)))
            except Exception as e:
                failed.append(FailedFile(file=p.file, reason=f"analysis error: {type(e).__name__}: {e}"))

        # ---- 3. TF-IDF Matrix Computation ----
        corpus = [b.text for p, _, _ in analysed for b in p.blocks] or [p.text for p, _, _ in analysed] or ["empty"]
        sem = SemanticScorer(corpus, settings)

        rejected: list[CandidateResult] = []
        eligible: list[tuple[ParsedResume, DetScore, Eligibility]] = []
        for p, hits, el in analysed:
            if not el.eligible:
                rejected.append(CandidateResult(
                    candidate_name=p.name, file=p.file, email=p.email, eligible=False,
                    rejection_reasons=el.reasons, matched_skills=el.matched_skills, warnings=p.warnings,
                    jd_similarity=round(sem.jd_similarity(p.evidence_text), 3)))
                continue
            try:
                eligible.append((p, score_candidate(p, hits, el, sem, settings), el))
            except Exception as e:
                failed.append(FailedFile(file=p.file, reason=f"scoring error: {type(e).__name__}: {e}"))

        # ---- 4. GitHub + LLM Enrichment (I/O-bound via asyncio + httpx) ----
        enrich_results: list[tuple[GitHubResult, LLMJudgement | None]] = []
        llm_on = False

        if eligible:
            async with httpx.AsyncClient() as http:
                gh = GitHubEnricher(settings.github) if (use_github and settings.github.enabled) else None
                client = make_client(settings.llm, http)
                judge = LLMJudge(client, settings.llm) if client else None
                llm_on = judge is not None

                async def one(parsed: ParsedResume) -> tuple[GitHubResult, LLMJudgement | None]:
                    gh_task = (
                        gh.enrich(http, parsed.github_username)
                        if gh else asyncio.sleep(0, result=GitHubResult(
                            username=parsed.github_username,
                            profile_url=parsed.github_url,
                            status="disabled",
                            summary="GitHub enrichment disabled"
                        ))
                    )
                    llm_task = judge.judge(parsed.text) if judge else asyncio.sleep(0, result=None)
                    g, l = await asyncio.gather(gh_task, llm_task, return_exceptions=True)
                    if isinstance(g, Exception):
                        g = GitHubResult(username=parsed.github_username, status="error", detail=repr(g), summary="GitHub enrichment failed")
                    if isinstance(l, Exception):
                        l = None
                    return g, l

                enrich_results = await asyncio.gather(*(one(p) for p, _, _ in eligible))
                if gh:
                    # Offload cache persistence to thread pool (disk I/O)
                    await loop.run_in_executor(self._thread_pool, gh.save_cache)

        # ---- 5. Blending & Final Ranking ----
        ranked: list[CandidateResult] = []
        gh_ok = gh_fail = llm_ok = llm_fail = 0
        for (p, det, el), (gh, judgement) in zip(eligible, enrich_results):
            status = "disabled"
            if llm_on:
                det, status = blend_llm(det, judgement, settings)
                llm_ok += status.startswith("ok")
                llm_fail += status == "failed"
            if gh.username:
                gh_ok += gh.status == "ok"
                gh_fail += gh.status in ("error", "rate_limited", "not_found")
            det.breakdown.github = round(min(settings.weights.github, gh.score), 1)
            total = total_score(det.breakdown, det.penalties, settings)
            concerns = list(det.concerns)
            if gh.status in ("no_profile",):
                concerns.append("No GitHub profile in resume (no score, not penalised beyond 0 GitHub points)")
            elif gh.status != "ok" and gh.status != "disabled":
                concerns.append(f"GitHub enrichment unavailable ({gh.status})")
            ranked.append(CandidateResult(
                candidate_name=p.name, file=p.file, email=p.email, eligible=True, total_score=total,
                score_breakdown=det.breakdown, penalties=det.penalties, matched_skills=el.matched_skills,
                project_summary=det.project_summary, evidence=det.evidence, github=gh, github_summary=gh.summary,
                strengths=det.strengths, concerns=concerns[:7], jd_similarity=det.jd_similarity,
                llm_status=status, warnings=p.warnings))

        ranked.sort(key=lambda c: (-c.total_score, -c.score_breakdown.ai_project_depth, -(c.jd_similarity or 0), c.candidate_name))
        for i, c in enumerate(ranked, 1):
            c.rank = i
        rejected.sort(key=lambda c: c.file)

        summary = BatchSummary(
            total_files=len(paths), parsed_ok=len(parsed_all) + len(duplicates), duplicates_skipped=len(duplicates),
            failed_unreadable=len(failed), eligible=len(ranked), rejected=len(rejected),
            github_ok=gh_ok, github_failed=gh_fail, llm_ok=llm_ok, llm_failed=llm_fail,
            runtime_seconds=round(time.perf_counter() - t0, 2))

        if on_progress:
            on_progress(total_files, total_files)

        return {
            "summary": summary.model_dump(),
            "ranked_candidates": [c.model_dump(mode="json") for c in ranked],
            "rejected_candidates": [c.model_dump(mode="json") for c in rejected],
            "failed_files": [f.model_dump() for f in failed],
            "duplicate_files": duplicates,
        }
