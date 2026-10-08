"""Layout-tolerant resume structuring: name, e-mail, GitHub, sections and project/experience blocks."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .ingest import RawResume

SECTION_ALIASES = {
    "summary": ["summary", "professional summary", "profile", "objective", "career objective", "about me", "about"],
    "skills": ["skills", "technical skills", "core competencies", "technologies", "tech stack", "skills & tools",
               "skills and tools", "technical proficiency", "tools & technologies", "key skills"],
    "experience": ["experience", "work experience", "professional experience", "employment", "work history",
                   "internships", "internship experience", "employment history", "relevant experience"],
    "projects": ["projects", "key projects", "personal projects", "academic projects", "selected projects",
                 "project experience", "notable projects", "side projects", "technical projects"],
    "education": ["education", "academic background", "academics", "qualifications", "academic qualifications"],
    "certifications": ["certifications", "certificates", "courses", "licenses", "licenses & certifications",
                       "training", "coursework"],
    "other": ["achievements", "awards", "honors", "extracurricular", "extracurriculars", "activities", "leadership",
              "publications", "positions of responsibility", "hobbies", "interests", "languages",
              "open source", "open-source contributions", "accomplishments", "volunteering"],
}
_ALIAS_TO_SECTION = {a: s for s, al in SECTION_ALIASES.items() for a in al}
NON_EVIDENCE_SECTIONS = {"education", "certifications"}   # "Python" in a course list is not evidence

BULLET_RE = re.compile(r"^\s*[•\-\*]\s*")
DATE_RE = re.compile(r"\b(?:19|20)\d{2}\b|\bpresent\b|\bcurrent\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
GH_RE = re.compile(r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?:/([\w.-]+))?", re.I)
GH_RESERVED = {"features", "about", "pricing", "topics", "marketplace", "sponsors", "orgs", "settings", "login",
               "enterprise", "collections", "explore", "apps", "notifications", "issues", "pulls", "security",
               "trending", "readme", "site", "contact", "customer-stories"}


@dataclass
class Block:
    title: str
    section: str
    bullets: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join([self.title, *self.bullets]).strip()


@dataclass
class ParsedResume:
    file: str
    name: str
    email: str | None
    github_username: str | None
    github_url: str | None
    text: str
    sections: dict[str, str]
    blocks: list[Block]
    warnings: list[str] = field(default_factory=list)

    @property
    def skills_text(self) -> str:           # claims (not implementation evidence)
        return "\n".join(self.sections.get(k, "") for k in ("skills", "summary"))

    @property
    def evidence_text(self) -> str:
        return "\n".join(t for k, t in self.sections.items() if k not in NON_EVIDENCE_SECTIONS)


def _heading_of(line: str) -> str | None:
    s = line.strip().strip(":").strip()
    if not s or len(s.split()) > 5 or BULLET_RE.match(s) or re.search(r"[|\d@]", s):
        return None
    norm = re.sub(r"[^a-z& ]", "", s.lower()).strip()
    if norm in _ALIAS_TO_SECTION:
        return _ALIAS_TO_SECTION[norm]
    first = norm.split(" & ")[0].strip()
    if (s.isupper() or s.istitle()) and first in _ALIAS_TO_SECTION:
        return _ALIAS_TO_SECTION[first]
    return None


def split_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {"header": []}
    cur = "header"
    for line in text.split("\n"):
        h = _heading_of(line)
        if h:
            cur = h
            sections.setdefault(cur, [])
            continue
        sections.setdefault(cur, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if "".join(v).strip()}


def _is_continuation(prev: str, line: str) -> bool:
    if line[:1].islower():
        return True
    if prev.rstrip().endswith((".", "!", "?", ":", ";")):
        return False
    return not (DATE_RE.search(line) or "|" in line or " — " in line or " – " in line)


def build_blocks(section_text: str, section: str) -> list[Block]:
    blocks: list[Block] = []
    cur: Block | None = None
    in_bullet = False
    for raw in section_text.split("\n"):
        s = raw.strip()
        if not s:
            continue
        if BULLET_RE.match(s):
            if cur is None:
                cur = Block("", section)
                blocks.append(cur)
            cur.bullets.append(BULLET_RE.sub("", s))
            in_bullet = True
        elif in_bullet and cur is not None and cur.bullets and _is_continuation(cur.bullets[-1], s):
            cur.bullets[-1] += " " + s
        else:
            if cur is not None and not cur.bullets:
                cur.title = f"{cur.title} | {s}".strip(" |")
            else:
                cur = Block(s, section)
                blocks.append(cur)
            in_bullet = False
    return blocks


def extract_name(lines: list[str], email: str | None, filename: str) -> str:
    for ln in lines[:8]:
        s = ln.strip()
        if not s or "@" in s or re.search(r"\d", s) or len(s) > 50:
            continue
        if re.search(r"resume|curriculum|vitae|engineer|developer|summary|profile|intern|\||/", s, re.I):
            continue
        words = s.split()
        if 2 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z.'’-]+", w) for w in words):
            return s.title() if s.isupper() else s
    if email:
        return re.sub(r"[\d_.]+", " ", email.split("@")[0]).strip().title() or Path(filename).stem
    return Path(filename).stem


def extract_github(text: str, links: list[str]) -> tuple[str | None, str | None]:
    """Prefer hyperlink annotations (survive 'GitHub' link-text); fall back to visible URLs."""
    counts: Counter[str] = Counter()
    for source, weight in ((links, 3), ([text], 1)):
        for chunk in source:
            for m in GH_RE.finditer(chunk):
                user = m.group(1)
                if user.lower() in GH_RESERVED:
                    continue
                counts[user] += weight + (1 if not m.group(2) else 0)   # bare profile URL > repo URL
    if not counts:
        return None, None
    user = counts.most_common(1)[0][0]
    return user, f"https://github.com/{user}"


def parse_resume(raw: RawResume) -> ParsedResume:
    text = raw.text
    email_m = EMAIL_RE.search(text)
    email = email_m.group(0).rstrip(".") if email_m else None
    if not email:
        email = next((l[7:] for l in raw.links if l.startswith("mailto:")), None)
    sections = split_sections(text)
    warnings: list[str] = []
    blocks: list[Block] = []
    for sec, body in sections.items():
        if sec in ("experience", "projects", "other"):
            blocks += build_blocks(body, sec)
    if not blocks:   # no recognisable headings: treat everything outside header/skills/education as one block
        warnings.append("No section headings recognised; using whole text as a single block")
        body = "\n".join(t for k, t in sections.items() if k not in NON_EVIDENCE_SECTIONS | {"skills"})
        blocks = build_blocks(body or text, "unknown")
    gh_user, gh_url = extract_github(text, raw.links)
    return ParsedResume(
        file=raw.path.name,
        name=extract_name(text.split("\n"), email, raw.path.name),
        email=email, github_username=gh_user, github_url=gh_url,
        text=text, sections=sections, blocks=blocks, warnings=warnings,
    )
