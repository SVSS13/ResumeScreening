"""TF-IDF semantic layer.

Fitted on the *whole batch* (all project/experience blocks + rubric prototypes + job description), so
terms that appear in every resume ("python", "developed", "api") get low IDF while distinctive terms
("langgraph", "reranking", "checkpoint") dominate. Used for:
  1. depth_sim - cosine(block, "ideal agentic/RAG project") -> up to `semantic_max_points` of ai depth
  2. thin_sim  - cosine(block, "thin API wrapper")          -> subtracted, so wrappers don't score as deep
  3. jd_similarity - cosine(resume, job description)        -> tie-breaker / explainability only
"""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from .config import Settings

_TOKEN = r"(?u)\b[a-zA-Z][a-zA-Z0-9+#.\-]{1,}\b"


class SemanticScorer:
    def __init__(self, corpus: list[str], settings: Settings):
        self._vec = TfidfVectorizer(
            ngram_range=(1, 2), sublinear_tf=True, stop_words="english",
            token_pattern=_TOKEN, lowercase=True, norm="l2",
        )
        docs = [*corpus, settings.depth_prototype, settings.thin_prototype, settings.job_description]
        self._vec.fit(docs)
        self._depth = self._vec.transform([settings.depth_prototype])
        self._thin = self._vec.transform([settings.thin_prototype])
        self._jd = self._vec.transform([settings.job_description])
        self._terms = self._vec.get_feature_names_out()
        self._s = settings

    def block_similarity(self, text: str) -> tuple[float, float]:
        v = self._vec.transform([text])
        return float(linear_kernel(v, self._depth)[0, 0]), float(linear_kernel(v, self._thin)[0, 0])

    def semantic_points(self, text: str) -> tuple[float, float, float]:
        depth, thin = self.block_similarity(text)
        raw = (depth - 0.6 * thin) / self._s.semantic_scale
        return self._s.semantic_max_points * max(0.0, min(1.0, raw)), depth, thin

    def jd_similarity(self, text: str) -> float:
        return float(linear_kernel(self._vec.transform([text]), self._jd)[0, 0])

    def distinctive_terms(self, text: str, k: int = 8) -> list[str]:
        row = self._vec.transform([text]).tocoo()
        pairs = sorted(zip(row.data, row.col), reverse=True)
        return [self._terms[c] for _, c in pairs[:k]]
