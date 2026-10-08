"""Scan a parsed resume against the lexicon: which skills, and WHERE (project block vs skills list)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .lexicon import AI_TOOL_NOISE, PYTHON_IMPLIED_TIERS, SKILLS, rx
from .parse import ParsedResume

_AGENT_BARE = rx(r"\bagents?\b")


@dataclass
class SkillHit:
    name: str
    tier: str
    blocks: set[int] = field(default_factory=set)   # indices of blocks (implementation evidence)
    in_claims: bool = False                         # skills list / summary only

    @property
    def anywhere(self) -> bool:
        return bool(self.blocks) or self.in_claims


def clean(text: str) -> str:
    """Remove 'I use AI tools' phrasing so Copilot/AI-assisted-dev never counts as AI engineering."""
    return AI_TOOL_NOISE.sub(" ", text)


def scan(parsed: ParsedResume) -> dict[str, SkillHit]:
    hits: dict[str, SkillHit] = {}

    def record(name: str, tier: str, block: int | None):
        h = hits.setdefault(name, SkillHit(name, tier))
        if block is None:
            h.in_claims = True
        else:
            h.blocks.add(block)

    claims = clean(parsed.skills_text)
    for name, (pattern, tier) in SKILLS.items():
        if pattern.search(claims):
            record(name, tier, None)
    for i, block in enumerate(parsed.blocks):
        text = clean(block.text)
        found_ai = False
        for name, (pattern, tier) in SKILLS.items():
            if pattern.search(text):
                record(name, tier, i)
                found_ai |= tier in ("strong", "llm")
        if found_ai and _AGENT_BARE.search(text):        # bare "agent" only counts next to AI context
            record("Agents", "strong", i)
    return hits


def by_tier(hits: dict[str, SkillHit], *tiers: str, in_blocks: bool | None = None) -> list[SkillHit]:
    out = []
    for h in hits.values():
        if h.tier not in tiers:
            continue
        if in_blocks is True and not h.blocks:
            continue
        if in_blocks is False and not (h.in_claims and not h.blocks):
            continue
        out.append(h)
    return out


def python_evidence(hits: dict[str, SkillHit]) -> tuple[str, list[str]]:
    """('explicit'|'implied'|'none', names)."""
    if "Python" in hits and hits["Python"].anywhere:
        return "explicit", ["Python"]
    implied = [h.name for h in hits.values() if h.tier in PYTHON_IMPLIED_TIERS and h.anywhere]
    return ("implied", implied) if implied else ("none", [])
