"""File discovery + text extraction. Every loader is failure-isolated: it never raises."""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)
SUPPORTED = {".pdf", ".docx", ".txt"}

_BULLETS = "•●▪■◦‣∙○◆►➢"


@dataclass
class RawResume:
    path: Path
    text: str = ""
    links: list[str] = field(default_factory=list)
    error: str | None = None


def discover(input_dir: str | Path) -> list[Path]:
    root = Path(input_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Input directory not found: {root}")
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith((".", "~$")))


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Co", "Cc", "Cf") or ch in "\n\t")
    for b in _BULLETS:
        text = text.replace(b, "•")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)              # de-hyphenate wrapped words
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_pdf(path: Path) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    links: list[str] = []
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                try:   # x_tolerance=1.5 fixes "words-glued-together" PDFs
                    text_parts.append(page.extract_text(x_tolerance=1.5) or "")
                except Exception as e:                   # one bad page must not kill the file
                    log.debug("page failed in %s: %s", path.name, e)
                try:
                    links += [h["uri"] for h in page.hyperlinks if h.get("uri")]
                except Exception:
                    pass
    except Exception as e:
        log.debug("pdfplumber failed on %s: %s", path.name, e)
    if not "".join(text_parts).strip():                  # fallback extractor
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text_parts = [(pg.extract_text() or "") for pg in reader.pages]
        except Exception as e:
            raise ValueError(f"PDF unreadable: {type(e).__name__}: {e}") from e
    return "\n".join(text_parts), links


def _read_docx(path: Path) -> tuple[str, list[str]]:
    import docx   # python-docx (bonus format)
    d = docx.Document(str(path))
    lines = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            lines.append(" | ".join(c.text for c in row.cells))
    links = [r.target_ref for r in d.part.rels.values() if "hyperlink" in r.reltype]
    return "\n".join(lines), links


def load_resume(path: Path, min_chars: int = 80) -> RawResume:
    try:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text, links = _read_pdf(path)
        elif suffix == ".docx":
            text, links = _read_docx(path)
        else:
            text, links = path.read_text(errors="ignore"), []
        text = normalize_text(text)
        if len(text) < min_chars:
            return RawResume(path, error="No extractable text (scanned image or empty file?)")
        return RawResume(path, text=text, links=links)
    except Exception as e:
        return RawResume(path, error=f"{type(e).__name__}: {e}")
