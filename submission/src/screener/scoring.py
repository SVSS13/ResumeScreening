"""Deterministic, evidence-based 100-point scoring model (+ optional LLM blend).

Principle: reward evidence inside project/experience blocks, never a bare skills-list keyword.
Every number added to the score also appends a human-readable reason (strengths/concerns/evidence).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Settings
from .eligibility import Eligibility
from .features import SkillHit, clean
from .lexicon import (ASYNC_RX, BACKEND_PRACTICE, DEPLOY_RX, DEPTH_DIMS, ENG_DEPTH, OWNERSHIP_VERBS,
                      STRUCTURAL_DIMS, TUTORIAL_CUES)
from .models import LLMJudgement, Penalty, ScoreBreakdown
from .parse import ParsedResume
from .tfidf import SemanticScorer


@dataclass
class DetScore:
    breakdown: ScoreBreakdown
    penalties: list[Penalty] = field(default_factory=list)
    project_summary: str = ""
    evidence: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    best_dims: list[str] = field(default_factory=list)
    jd_similarity: float = 0.0


def _tier_hits(hits, *names):
    return [hits[n] for n in names if n in hits]


def _pts(hit: SkillHit | None, in_blocks: float, in_claims: float) -> float:
    if hit is None:
        return 0.0
    return in_blocks if hit.blocks else (in_claims if hit.in_claims else 0.0)


def _trim(s: str, n: int = 220) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _clean_title(t: str) -> str:
    t = re.sub(r"\s*\(?\b(?:\d{1,2}/)?(?:19|20)\d{2}\b.*$|\s+\d{1,2}/\S*.*$|\s+[A-Z][a-z]{2,8}\.? ?(?:19|20)\d{2}.*$", "", t)
    t = re.sub(r"\s+(?:GitHub|Live Link|Demo)\b.*$", "", t)
    return _trim(t.split("|")[0].strip(" -—–|"), 80) or "Project"


def _ai_blocks(parsed: ParsedResume, hits: dict[str, SkillHit]) -> list[int]:
    idx = set()
    for h in hits.values():
        if h.tier in ("strong", "llm"):
            idx |= h.blocks
    return sorted(idx)


def _score_block(i: int, parsed: ParsedResume, hits, sem: SemanticScorer, settings: Settings) -> dict:
    text = clean(parsed.blocks[i].text)
    strong = sorted({h.name for h in hits.values() if h.tier == "strong" and i in h.blocks})
    llm = sorted({h.name for h in hits.values() if h.tier == "llm" and i in h.blocks})
    dims = [d for d, rx_ in DEPTH_DIMS.items() if rx_.search(text)]
    sem_pts, depth_sim, thin_sim = sem.semantic_points(text)
    base = 6 if strong else 3
    extra = min(2, max(0, len(strong) - 1))
    score = min(40.0, base + 3.5 * len(dims) + extra + sem_pts)
    return {"i": i, "score": score, "dims": dims, "strong": strong, "llm": llm,
            "sem": sem_pts, "text": text}


def _best_bullets(block_text: str, n: int = 2) -> list[str]:
    from .lexicon import SKILLS
    ranked = []
    for line in block_text.split("\n")[1:]:
        k = sum(bool(rx_.search(line)) for rx_ in DEPTH_DIMS.values())
        k += sum(bool(p.search(line)) for p, t in SKILLS.values() if t in ("strong", "llm"))
        ranked.append((k, line))
    ranked.sort(key=lambda x: -x[0])
    return [_trim(l) for k, l in ranked[:n] if k > 0] or [_trim(block_text.split("\n")[0])]


def score_candidate(parsed: ParsedResume, hits: dict[str, SkillHit], elig: Eligibility,
                    sem: SemanticScorer, settings: Settings) -> DetScore:
    w, pc = settings.weights, settings.penalties
    out = DetScore(breakdown=ScoreBreakdown())
    strengths, concerns = out.strengths, out.concerns

    # ---------------- 1. AI / agentic / RAG project depth (40) ----------------
    ai_idx = _ai_blocks(parsed, hits)
    scored = sorted((_score_block(i, parsed, hits, sem, settings) for i in ai_idx), key=lambda b: -b["score"])
    if elig.ai_level == "implemented" and scored:
        s = [b["score"] for b in scored] + [0, 0]
        ai_pts = min(w.ai_project_depth, s[0] + 0.2 * s[1] + 0.1 * s[2])
        best = scored[0]
        out.best_dims = best["dims"]
        out.evidence = _best_bullets(best["text"])
        out.project_summary = f"{_clean_title(parsed.blocks[best['i']].title)}: {out.evidence[0]}"
        stack = ", ".join((best["strong"] + best["llm"])[:5])
        strengths.append(f"AI project '{_clean_title(parsed.blocks[best['i']].title)}' uses {stack}"
                         + (f" with {', '.join(d.replace('_', ' ') for d in best['dims'] if d != 'production')}"
                            if any(d != "production" for d in best["dims"]) else ""))
        # --- project-quality penalties (assignment section 4) ---
        structural = [d for d in best["dims"] if d in STRUCTURAL_DIMS]
        if not structural:
            out.penalties.append(Penalty(reason="Thin LLM wrapper: best AI project shows no retrieval, "
                                         "orchestration/state, tool use, evaluation or data-processing",
                                         points=pc.thin_wrapper_no_depth))
        elif len(structural) == 1 and not best["strong"]:
            out.penalties.append(Penalty(reason="Shallow AI project: single depth signal and no agent/RAG framework",
                                         points=pc.thin_wrapper_one_dim))
        btxt = parsed.blocks[best["i"]].text
        if TUTORIAL_CUES.search(btxt) or not OWNERSHIP_VERBS.search(btxt):
            out.penalties.append(Penalty(reason="Tutorial-style / no ownership evidence in best AI project",
                                         points=pc.tutorial_style))
        if "evaluation" not in best["dims"]:
            concerns.append("No evaluation/metrics evidence in the AI project")
    elif elig.ai_level == "skills_only":
        ai_pts = min(6.0, 2.0 * len(elig.ai_terms))
        out.penalties.append(Penalty(reason="AI frameworks listed only in skills/summary, no implementation evidence",
                                     points=pc.skills_only_ai))
        concerns.append("AI keywords appear only in the skills list")
        out.project_summary = "No AI project found; AI/LLM terms appear only in skills/summary."
    else:
        ai_pts = 6.0 if elig.ai_level == "ml_only" else 0.0
    out.breakdown.ai_project_depth = ai_pts

    # ---------------- 2. Python & backend engineering (30) ----------------
    py_blocks: set[int] = set()
    for h in hits.values():
        if h.tier in ("python", "pyfw", "pylib") or h.name in ("LangGraph", "LangChain", "LlamaIndex", "PyTorch",
                                                                 "Scikit-learn"):
            py_blocks |= h.blocks
    py = 3.0 if elig.python_level != "none" else 0.0
    py += 6.0 * min(len(py_blocks), 2)
    py += _pts(hits.get("FastAPI"), 5, 2) + max(_pts(hits.get("Flask"), 3, 1), _pts(hits.get("Django"), 3, 1))
    block_text = "\n".join(clean(b.text) for b in parsed.blocks)
    if ASYNC_RX.search(block_text):
        py += 3
    if hits.get("PostgreSQL"):
        py += _pts(hits["PostgreSQL"], 3, 1)
    elif hits.get("MySQL/SQLite/SQL"):
        py += _pts(hits["MySQL/SQLite/SQL"], 1.5, 0.5)
    py += _pts(hits.get("Redis"), 3, 1)
    py += min(2, len(set(m.lower() for m in BACKEND_PRACTICE.findall(block_text))))
    out.breakdown.python_backend = min(w.python_backend, py)
    if hits.get("FastAPI") and hits["FastAPI"].blocks:
        strengths.append("FastAPI used in project/experience work")
    if len(py_blocks) >= 2:
        strengths.append("Python used across multiple projects/roles")
    for name in ("PostgreSQL", "Redis"):
        if not (hits.get(name) and hits[name].blocks):
            concerns.append(f"Limited {name} evidence")

    # ---------------- 3. Cloud / deployment / full-stack (15) ----------------
    cl = _pts(hits.get("GCP"), 4, 1.5)
    cl += max(_pts(hits.get("AWS"), 2.5, 1), _pts(hits.get("Azure"), 2.5, 1)) if not hits.get("GCP") else 0
    cl += _pts(hits.get("Docker"), 4, 1.5) + _pts(hits.get("Kubernetes"), 1.5, 0.5) + _pts(hits.get("CI/CD"), 1, 0.5)
    if DEPLOY_RX.search(block_text):
        cl += 2
    fe = [n for n in ("React", "Next.js") if hits.get(n) and hits[n].blocks]
    if fe and elig.python_level != "none":
        cl += 2                                           # supporting signal only
    out.breakdown.cloud_fullstack = min(w.cloud_fullstack, cl)
    if hits.get("Docker") and hits["Docker"].blocks and (hits.get("GCP") or hits.get("AWS") or hits.get("Azure")):
        strengths.append("Containerised/cloud deployment experience")
    if not (hits.get("Docker") and hits["Docker"].anywhere):
        concerns.append("No Docker/deployment evidence")

    # ---------------- 5. Engineering depth signals (5) ----------------
    cats = [c for c, rx_ in ENG_DEPTH.items() if rx_.search(block_text)]
    out.breakdown.engineering_depth = float(min(w.engineering_depth, len(cats)))
    if "testing" not in cats:
        concerns.append("No testing evidence")
    if len(cats) >= 3:
        strengths.append("Non-trivial engineering signals: " + ", ".join(cats[:4]))

    out.jd_similarity = round(sem.jd_similarity(parsed.evidence_text), 3)
    for p in out.penalties:  # keep concerns aligned with penalties
        concerns.append(p.reason)
    out.concerns = list(dict.fromkeys(concerns))[:6]
    out.strengths = list(dict.fromkeys(strengths))[:5]
    return out


def blend_llm(det: DetScore, judgement: LLMJudgement | None, settings: Settings) -> tuple[DetScore, str]:
    """Hybrid scoring: LLM refines project quality but is bounded by deterministic evidence."""
    if judgement is None:
        return det, "failed"
    cfg, base = settings.llm, det.breakdown.ai_project_depth
    if base <= 0:
        return det, "ok(no-op: no deterministic AI evidence)"
    llm_pts = judgement.overall_ai_depth / 10 * settings.weights.ai_project_depth
    blended = (1 - cfg.blend) * base + cfg.blend * llm_pts
    det.breakdown.ai_project_depth = round(min(settings.weights.ai_project_depth, base + cfg.max_uplift, blended), 1)
    kinds = {p.kind for p in judgement.projects if p.depth >= 4}
    if (not kinds & {"agentic", "rag"} and judgement.projects
            and all(p.kind in ("llm_wrapper", "tutorial") for p in judgement.projects)
            and not any(p.reason.startswith("Thin") for p in det.penalties)):
        det.penalties.append(Penalty(reason="LLM judge: AI projects are thin wrappers/tutorial-style",
                                     points=settings.penalties.thin_wrapper_no_depth))
    if judgement.summary:
        det.project_summary = judgement.summary
    det.strengths = list(dict.fromkeys(judgement.strengths + det.strengths))[:5]
    det.concerns = list(dict.fromkeys(judgement.concerns + det.concerns))[:6]
    return det, "ok"


def total_score(breakdown: ScoreBreakdown, penalties: list[Penalty], settings: Settings) -> float:
    pen = min(settings.penalties.max_total, sum(p.points for p in penalties))
    total = sum(breakdown.model_dump().values()) - pen
    return round(max(0.0, min(100.0, total)), 1)
