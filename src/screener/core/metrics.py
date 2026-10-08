"""Metrics and telemetry collection for caching, rate limiting, and memory usage."""
from __future__ import annotations

import itertools
import threading
import time
from typing import Any

from .memory_guard import EvictableCache, get_process_rss_mb


class MetricsCollector:
    """Thread-safe telemetry collector for service observability."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._registered_caches: dict[str, EvictableCache] = {}
        self._inbound_rate_limited = itertools.count()
        self._outbound_throttled = itertools.count()

        self._rate_limited_total: int = 0
        self._throttled_total: int = 0

    def register_cache(self, name: str, cache: EvictableCache) -> None:
        with self._lock:
            self._registered_caches[name] = cache

    def record_rate_limit(self) -> None:
        with self._lock:
            self._rate_limited_total += 1

    def record_outbound_throttle(self) -> None:
        with self._lock:
            self._throttled_total += 1

    def snapshot(self) -> dict[str, Any]:
        """Produce a comprehensive telemetry snapshot suitable for GET /metrics."""
        with self._lock:
            caches = list(self._registered_caches.items())
            rate_limited = self._rate_limited_total
            throttled = self._throttled_total

        total_entries = 0
        total_bytes = 0
        total_hits = 0
        total_misses = 0
        total_evictions = 0
        total_expirations = 0

        cache_details: dict[str, dict[str, Any]] = {}
        for name, cache in caches:
            s = cache.stats()
            hits = getattr(s, "hits", 0)
            misses = getattr(s, "misses", 0)
            evictions = getattr(s, "evictions", 0)
            expirations = getattr(s, "expirations", 0)
            b = getattr(s, "total_bytes", 0)
            cnt = getattr(s, "item_count", 0)

            total_hits += hits
            total_misses += misses
            total_evictions += evictions
            total_expirations += expirations
            total_bytes += b
            total_entries += cnt

            hr = round(hits / (hits + misses), 4) if (hits + misses) > 0 else 0.0
            cache_details[name] = {
                "entries": cnt,
                "bytes": b,
                "hits": hits,
                "misses": misses,
                "hit_ratio": hr,
                "evictions": evictions,
                "expirations": expirations,
            }

        overall_hit_ratio = (
            round(total_hits / (total_hits + total_misses), 4) if (total_hits + total_misses) > 0 else 0.0
        )

        return {
            "entries": total_entries,
            "bytes": total_bytes,
            "hits": total_hits,
            "misses": total_misses,
            "hit_ratio": overall_hit_ratio,
            "evictions": total_evictions,
            "expirations": total_expirations,
            "rss_mb": round(get_process_rss_mb(), 2),
            "rate_limited_total": rate_limited,
            "outbound_throttled_total": throttled,
            "uptime_seconds": round(time.time() - self._start_time, 2),
            "caches": cache_details,
        }
