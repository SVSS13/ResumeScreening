"""Cache adapters wrapping parse, GitHub, and LLM stages without modifying domain logic.

Complexity: O(1) cache lookups via ShardedLRUCache.
Thread-safety: Fully thread-safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from ..core.sharded_cache import ShardedLRUCache
from ..dedup import BloomFilter
from ..ingest import RawResume
from ..models import GitHubResult, LLMJudgement
from ..parse import ParsedResume, parse_resume

log = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


class ParseCacheAdapter:
    """Caches ParsedResume objects keyed by content sha256."""

    def __init__(self, cache: ShardedLRUCache[str, ParsedResume] | None = None) -> None:
        self._cache = cache or ShardedLRUCache[str, ParsedResume](
            num_shards=16,
            max_items=10_000,
            max_bytes=64 * 1024 * 1024,
            sizeof=lambda p: len(p.text) * 2,
        )

    def parse(self, raw: RawResume) -> ParsedResume:
        key = _content_hash(raw.text)
        return self._cache.get_or_load(key, lambda: parse_resume(raw))


class GitHubCacheAdapter:
    """Thread-safe ShardedLRUCache adapter for GitHub enrichments with atomic disk snapshotting."""

    def __init__(
        self,
        cache_path: str = ".cache/github.json",
        ttl_hours: float = 24.0,
        cache: ShardedLRUCache[str, list[dict]] | None = None,
    ) -> None:
        self._path = Path(cache_path)
        self._ttl_s = ttl_hours * 3600.0
        self._cache = cache or ShardedLRUCache[str, list[dict]](
            num_shards=16,
            max_items=5_000,
            max_bytes=32 * 1024 * 1024,
            ttl_s=self._ttl_s,
            sizeof=lambda repos: len(json.dumps(repos).encode()),
        )
        self._bloom = BloomFilter(capacity=10_000, fp_rate=0.01)
        self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for user, record in data.items():
                repos = record.get("repos")
                if repos:
                    self._cache.put(user.lower(), repos, ttl_s=self._ttl_s)
                    self._bloom.add(user.lower())
        except Exception as e:
            log.warning("Could not read disk cache %s: %s", self._path, e)

    def get(self, user: str) -> list[dict] | None:
        key = user.lower()
        return self._cache.get(key)

    def put(self, user: str, repos: list[dict]) -> None:
        key = user.lower()
        self._bloom.add(key)
        self._cache.put(key, repos, ttl_s=self._ttl_s)

    def save_snapshot(self) -> None:
        """Atomic snapshot write: writes to temporary file then replaces target."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".tmp")
            data: dict[str, Any] = {}
            # Collect from all shards
            for shard in self._cache._shards:
                with shard._lock:
                    for k, node in shard._map.items():
                        data[str(k)] = {"repos": node.val}
            tmp_path.write_text(json.dumps(data))
            os.replace(tmp_path, self._path)
        except Exception as e:
            log.warning("Could not persist atomic github cache snapshot: %s", e)


class LLMCacheAdapter:
    """Caches LLMJudgement objects keyed by sha256(model + prompt + text)."""

    def __init__(self, cache: ShardedLRUCache[str, LLMJudgement] | None = None) -> None:
        self._cache = cache or ShardedLRUCache[str, LLMJudgement](
            num_shards=16,
            max_items=5_000,
            max_bytes=32 * 1024 * 1024,
            sizeof=lambda j: 512,
        )

    def get(self, key: str) -> LLMJudgement | None:
        return self._cache.get(key)

    def put(self, key: str, judgement: LLMJudgement) -> None:
        self._cache.put(key, judgement)
