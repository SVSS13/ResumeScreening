"""Orchestration: ingest -> dedup -> parse -> hard filter -> TF-IDF fit -> score -> enrich -> rank.

Failure isolation: every per-resume step is wrapped; a bad file becomes an entry in `failed_files`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from .config import Settings
from .dedup import DuplicateDetector
from .eligibility import Eligibility, check_eligibility
from .features import scan
from .github_enrich import GitHubEnricher
from .ingest import discover, load_resume
from .llm import LLMJudge, make_client
from .models import (BatchSummary, CandidateResult, FailedFile, GitHubResult, LLMJudgement)
from .parse import ParsedResume, parse_resume
from .scoring import DetScore, blend_llm, score_candidate, total_score
from .tfidf import SemanticScorer

log = logging.getLogger(__name__)


async def _enrich(settings: Settings, eligible: list[tuple[ParsedResume, DetScore]], use_github: bool):
    """GitHub + LLM enrichment, concurrently, with bounded concurrency inside each component."""
    async with httpx.AsyncClient() as http:
        gh = GitHubEnricher(settings.github) if (use_github and settings.github.enabled) else None
        client = make_client(settings.llm, http)
        judge = LLMJudge(client, settings.llm) if client else None

        async def one(parsed: ParsedResume):
            gh_job = gh.enrich(http, parsed.github_username) if gh else _const(
                GitHubResult(username=parsed.github_username, profile_url=parsed.github_url, status="disabled",
                             summary="GitHub enrichment disabled"))
            llm_job = judge.judge(parsed.text) if judge else _const(None)
            g, l = await asyncio.gather(gh_job, llm_job, return_exceptions=True)
            if isinstance(g, Exception):
                g = GitHubResult(username=parsed.github_username, status="error", detail=repr(g),
                                 summary="GitHub enrichment failed")
            if isinstance(l, Exception):
                l = None
            return g, l

        results = await asyncio.gather(*(one(p) for p, _ in eligible))
        if gh:
            gh.save_cache()
    return results, judge is not None


async def _const(v):
    return v


def run(input_dir: str | Path, settings: Settings, use_github: bool = True) -> dict:
    t0 = time.perf_counter()
    paths = discover(input_dir)
    failed: list[FailedFile] = []
    duplicates: list[dict] = []
    dedup = DuplicateDetector(settings.bloom_capacity, settings.bloom_fp_rate)
    parsed_all: list[ParsedResume] = []

    # ---- 1-2. ingest, dedup, parse (each file isolated) ----
    for path in paths:
        raw = load_resume(path)
        if raw.error:
            failed.append(FailedFile(file=path.name, reason=raw.error))
            continue
        try:
            parsed = parse_resume(raw)
            dup_of = dedup.check_and_add(path.name, raw.text, parsed.email)
            if dup_of:
                duplicates.append({"file": path.name, "duplicate_of": dup_of})
                continue
            parsed_all.append(parsed)
        except Exception as e:
            failed.append(FailedFile(file=path.name, reason=f"parse error: {type(e).__name__}: {e}"))

    # ---- 3. hard eligibility (deterministic) ----
    analysed: list[tuple[ParsedResume, dict, Eligibility]] = []
    for p in parsed_all:
        try:
            hits = scan(p)
            analysed.append((p, hits, check_eligibility(p, hits, settings)))
        except Exception as e:
            failed.append(FailedFile(file=p.file, reason=f"analysis error: {type(e).__name__}: {e}"))

    # ---- 4. TF-IDF fitted on the entire batch (all blocks, eligible or not) ----
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

    # ---- 5. GitHub + LLM enrichment (async, bounded) ----
    enrich, llm_on = (asyncio.run(_enrich(settings, [(p, d) for p, d, _ in eligible], use_github))
                      if eligible else ([], False))

    ranked: list[CandidateResult] = []
    gh_ok = gh_fail = llm_ok = llm_fail = 0
    for (p, det, el), (gh, judgement) in zip(eligible, enrich):
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

    # deterministic ordering: score, AI depth, JD similarity, name
    ranked.sort(key=lambda c: (-c.total_score, -c.score_breakdown.ai_project_depth, -(c.jd_similarity or 0), c.candidate_name))
    for i, c in enumerate(ranked, 1):
        c.rank = i
    rejected.sort(key=lambda c: c.file)

    summary = BatchSummary(
        total_files=len(paths), parsed_ok=len(parsed_all) + len(duplicates), duplicates_skipped=len(duplicates),
        failed_unreadable=len(failed), eligible=len(ranked), rejected=len(rejected),
        github_ok=gh_ok, github_failed=gh_fail, llm_ok=llm_ok, llm_failed=llm_fail,
        runtime_seconds=round(time.perf_counter() - t0, 2))
    return {
        "summary": summary.model_dump(),
        "ranked_candidates": [c.model_dump(mode="json") for c in ranked],
        "rejected_candidates": [c.model_dump(mode="json") for c in rejected],
        "failed_files": [f.model_dump() for f in failed],
        "duplicate_files": duplicates,
    }
