"""All tunables live here (weights, thresholds, lexicon toggles, API settings).

Business logic never reads os.environ directly; it receives a `Settings` object.
Secrets come ONLY from environment variables (see .env.example).
"""
from __future__ import annotations

import dataclasses as dc
import json
import os
from pathlib import Path


@dc.dataclass(frozen=True)
class Weights:
    ai_project_depth: float = 40
    python_backend: float = 30
    cloud_fullstack: float = 15
    github: float = 10
    engineering_depth: float = 5


@dc.dataclass(frozen=True)
class PenaltyConfig:
    thin_wrapper_no_depth: float = 10      # best AI project has zero depth dimensions
    thin_wrapper_one_dim: float = 5        # only one depth dimension, no real framework
    tutorial_style: float = 5              # tutorial cues / no ownership evidence
    skills_only_ai: float = 5              # AI appears only in a skills list
    max_total: float = 15                  # assignment: deduct 5-15


@dc.dataclass(frozen=True)
class GitHubConfig:
    enabled: bool = True
    token_env: str = "GITHUB_TOKEN"
    max_concurrency: int = 5
    timeout_s: float = 10.0
    cache_path: str = ".cache/github.json"
    cache_ttl_hours: float = 24.0
    missing_score: float = 0.0             # score when no profile / API failure


@dc.dataclass(frozen=True)
class LLMConfig:
    provider: str = "none"                 # none | anthropic | openai
    model: str = "claude-haiku-5-5"
    api_key_env: str = "LLM_API_KEY"
    base_url: str = ""
    max_concurrency: int = 4
    timeout_s: float = 60.0
    max_retries: int = 2
    max_resume_chars: int = 12000
    blend: float = 0.4                     # weight of LLM depth in ai_project_depth
    max_uplift: float = 8.0                # LLM can add at most this many points over deterministic


@dc.dataclass(frozen=True)
class Settings:
    weights: Weights = Weights()
    penalties: PenaltyConfig = PenaltyConfig()
    github: GitHubConfig = GitHubConfig()
    llm: LLMConfig = LLMConfig()
    # Eligibility policy
    accept_classical_ml: bool = False      # sklearn/CNN-only profiles are NOT "AI/LLM/agentic"
    accept_skills_only_ai: bool = True     # eligible but heavily penalised (see PenaltyConfig)
    # Dedup
    bloom_fp_rate: float = 0.001
    bloom_capacity: int = 10_000
    # TF-IDF semantic scoring
    semantic_max_points: float = 8.0
    semantic_scale: float = 0.25           # similarity at which semantic points saturate
    depth_prototype: str = (
        "Built a stateful multi-agent workflow with LangGraph orchestration, tool calling and "
        "retrieval augmented generation over a vector database using embeddings, chunking and "
        "reranking. Added an evaluation pipeline with metrics, async FastAPI backend, caching, "
        "retries, observability and Docker deployment."
    )
    thin_prototype: str = (
        "Built a chatbot using the OpenAI API that answers user questions. Integrated ChatGPT "
        "API call with a prompt. Simple AI powered app wrapper."
    )
    job_description: str = (
        "SDE intern Python FastAPI async PostgreSQL Redis Docker GCP LangGraph LangChain RAG "
        "agentic multi-agent tool calling embeddings vector search LLM evaluation testing"
    )

    @classmethod
    def load(cls, config_path: str | None = None) -> "Settings":
        s = cls(
            llm=LLMConfig(
                provider=os.getenv("LLM_PROVIDER", "none").lower(),
                model=os.getenv("LLM_MODEL", LLMConfig.model),
                base_url=os.getenv("LLM_BASE_URL", ""),
            )
        )
        if config_path:
            s = _merge(s, json.loads(Path(config_path).read_text()))
        return s


def _merge(obj, overrides: dict):
    """Recursively apply a JSON dict of overrides onto a frozen dataclass tree."""
    changes = {}
    for key, val in overrides.items():
        cur = getattr(obj, key)
        changes[key] = _merge(cur, val) if dc.is_dataclass(cur) and isinstance(val, dict) else val
    return dc.replace(obj, **changes)
