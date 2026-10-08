"""Runtime Memory Management & Eviction Hysteresis.

Analogy:
- LRU ≈ page-replacement policy
- Cache budget ≈ RAM frames
- MemoryGuard ≈ OS page reclamation daemon (kswapd)

Eviction Hysteresis:
When total cache memory crosses the high watermark (default 80%), MemoryGuard evicts
tail entries across registered caches down to the low watermark (default 60%).
This creates a 20% headroom buffer and avoids eviction thrashing on subsequent inserts.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Protocol

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

try:
    import resource
except ImportError:
    resource = None  # type: ignore

log = logging.getLogger(__name__)


def get_process_rss_mb() -> float:
    """Read process Resident Set Size (RSS) in megabytes."""
    if psutil is not None:
        try:
            return float(psutil.Process().memory_info().rss) / (1024.0 * 1024.0)
        except Exception:
            pass
    if resource is not None:
        try:
            # On Linux, ru_maxrss is in KiB
            return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0
        except Exception:
            pass
    return 0.0


class EvictableCache(Protocol):
    def evict_to(self, target_bytes: int) -> int: ...
    def stats(self) -> Any: ...


class MemoryGuard:
    """Daemon thread monitoring cache bytes and process RSS with watermark hysteresis."""

    def __init__(
        self,
        cache_max_bytes: int = 256 * 1024 * 1024,  # 256 MB default
        high_watermark: float = 0.80,
        low_watermark: float = 0.60,
        rss_soft_limit_mb: float = 512.0,
        check_interval_s: float = 5.0,
    ) -> None:
        self.cache_max_bytes = cache_max_bytes
        self.high_watermark = high_watermark
        self.low_watermark = low_watermark
        self.rss_soft_limit_mb = rss_soft_limit_mb
        self.check_interval_s = check_interval_s

        self._high_bytes = int(cache_max_bytes * high_watermark)
        self._low_bytes = int(cache_max_bytes * low_watermark)

        self._caches: list[EvictableCache] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._eviction_cycles: int = 0
        self._total_evicted_items: int = 0

    def register_cache(self, cache: EvictableCache) -> None:
        with self._lock:
            if cache not in self._caches:
                self._caches.append(cache)

    def unregister_cache(self, cache: EvictableCache) -> None:
        with self._lock:
            if cache in self._caches:
                self._caches.remove(cache)

    def run_cycle(self) -> int:
        """Execute one check and eviction cycle. Returns count of evicted items."""
        rss_mb = get_process_rss_mb()
        with self._lock:
            caches = list(self._caches)

        if not caches:
            return 0

        # Query total bytes across registered caches
        total_cache_bytes = 0
        for c in caches:
            stats = c.stats()
            total_cache_bytes += getattr(stats, "total_bytes", 0)

        pressure = (total_cache_bytes > self._high_bytes) or (
            self.rss_soft_limit_mb > 0 and rss_mb > self.rss_soft_limit_mb
        )

        if not pressure:
            return 0

        # Evict down to low watermark (hysteresis headroom)
        target_total = self._low_bytes
        evicted_this_cycle = 0

        # Distribute target proportionally or equally across caches
        target_per_cache = max(0, target_total // len(caches))
        for c in caches:
            evicted = c.evict_to(target_per_cache)
            evicted_this_cycle += evicted

        self._eviction_cycles += 1
        self._total_evicted_items += evicted_this_cycle
        log.info(
            "MemoryGuard cycle: RSS=%.1fMB, initial_bytes=%d, evicted=%d items to low_watermark=%d",
            rss_mb,
            total_cache_bytes,
            evicted_this_cycle,
            self._low_bytes,
        )
        return evicted_this_cycle

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.wait(self.check_interval_s):
                try:
                    self.run_cycle()
                except Exception as e:
                    log.error("Error in MemoryGuard cycle: %s", e)

        self._thread = threading.Thread(target=_loop, daemon=True, name="MemoryGuardThread")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
