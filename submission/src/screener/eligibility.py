"""Hard, deterministic eligibility filter. No LLM is involved here by design (predictable + testable)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .features import SkillHit, by_tier, python_evidence
from .parse import NON_EVIDENCE_SECTIONS, ParsedResume
from .lexicon import SKILLS


@dataclass
class Eligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    python_level: str = "none"                   # explicit | implied | none
    ai_level: str = "none"                       # implemented | skills_only | ml_only | none
    ai_terms: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)


def check_eligibility(parsed: ParsedResume, hits: dict[str, SkillHit], settings: Settings) -> Eligibility:
    reasons: list[str] = []

    # --- Python evidence (education/coursework-only mentions do not count) ---
    py_level, _ = python_evidence(hits)
    if py_level == "none":
        edu_only = any(
            SKILLS["Python"][0].search(parsed.sections.get(k, "")) for k in NON_EVIDENCE_SECTIONS
        )
        reasons.append("Python only mentioned in education/coursework" if edu_only
                       else "No evidence of Python stack")

    # --- AI / LLM / agentic evidence ---
    strong_impl = by_tier(hits, "strong", in_blocks=True)
    llm_impl = by_tier(hits, "llm", in_blocks=True)
    strong_claim = by_tier(hits, "strong", in_blocks=False)
    llm_claim = by_tier(hits, "llm", in_blocks=False)
    ml_any = [h for h in by_tier(hits, "ml") if h.anywhere]

    if strong_impl or llm_impl:
        ai_level, terms = "implemented", [h.name for h in strong_impl + llm_impl]
    elif settings.accept_skills_only_ai and (strong_claim or len(llm_claim) >= 2):
        ai_level, terms = "skills_only", [h.name for h in strong_claim + llm_claim]
    elif ml_any:
        ai_level, terms = "ml_only", [h.name for h in ml_any]
    else:
        ai_level, terms = "none", []

    if ai_level == "ml_only" and settings.accept_classical_ml:
        ai_level_ok = True
    else:
        ai_level_ok = ai_level in ("implemented", "skills_only")
    if not ai_level_ok:
        reasons.append("Only classical ML/DL evidence; no LLM/RAG/agentic work" if ai_level == "ml_only"
                       else "No AI/agentic project evidence")

    matched = sorted(h.name for h in hits.values() if h.anywhere)
    return Eligibility(
        eligible=not reasons, reasons=reasons, python_level=py_level,
        ai_level=ai_level, ai_terms=sorted(set(terms)), matched_skills=matched,
    )
