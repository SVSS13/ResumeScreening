import tracemalloc
import pytest

from screener.core.lru_cache import LRUCache
from screener.core.memory_guard import MemoryGuard, get_process_rss_mb
from screener.core.metrics import MetricsCollector


def test_rss_measurement():
    rss = get_process_rss_mb()
    assert rss > 0.0


def test_memory_guard_hysteresis_eviction():
    # Cache with 1000 max bytes
    cache: LRUCache[str, str] = LRUCache(max_items=100, max_bytes=1000, sizeof=lambda s: len(s.encode()))
    # MemoryGuard: high=0.80 (800 bytes), low=0.60 (600 bytes)
    guard = MemoryGuard(cache_max_bytes=1000, high_watermark=0.80, low_watermark=0.60)
    guard.register_cache(cache)

    # 1. Fill cache to 700 bytes (below high watermark of 800)
    for i in range(7):
        cache.put(f"k{i}", "v" * 100)
    assert cache.stats().total_bytes == 700

    # Run cycle -> should NOT evict
    evicted = guard.run_cycle()
    assert evicted == 0
    assert cache.stats().total_bytes == 700

    # 2. Add two more items to reach 900 bytes (above high watermark of 800)
    cache.put("k7", "v" * 100)
    cache.put("k8", "v" * 100)
    assert cache.stats().total_bytes == 900

    # Run cycle -> should evict down to low watermark (600 bytes)
    evicted = guard.run_cycle()
    assert evicted == 3  # Evicted 3 items (300 bytes) -> down to 600 bytes
    assert cache.stats().total_bytes == 600


def test_metrics_collector():
    collector = MetricsCollector()
    cache: LRUCache[str, str] = LRUCache(max_items=10, max_bytes=1000)
    collector.register_cache("test_cache", cache)

    cache.put("a", "alpha")
    cache.get("a")
    cache.get("b")  # miss
    collector.record_rate_limit()

    snapshot = collector.snapshot()
    assert snapshot["entries"] == 1
    assert snapshot["hits"] == 1
    assert snapshot["misses"] == 1
    assert snapshot["hit_ratio"] == 0.5
    assert snapshot["rate_limited_total"] == 1
    assert "test_cache" in snapshot["caches"]


def test_boundedness_stress_and_flat_memory():
    """Stress test: Inserting 100,000 keys into LRUCache with strict byte ceiling.

    Verifies bytes <= max_bytes and tracemalloc memory stabilizes post warm-up.
    (Using 100k items in unit test for speed; 1M verified in benchmark suite).
    """
    max_items = 5_000
    max_bytes = 100_000  # 100 KB
    cache: LRUCache[str, str] = LRUCache(
        max_items=max_items,
        max_bytes=max_bytes,
        sizeof=lambda s: len(s),
    )

    tracemalloc.start()
    # Warm-up phase
    for i in range(5_000):
        cache.put(f"key_{i}", f"value_{i:04d}")

    # Check baseline memory after warm-up
    current_mem_1, peak_mem_1 = tracemalloc.get_traced_memory()

    # Insert 95,000 more keys (total 100,000 inserts)
    for i in range(5_000, 100_000):
        cache.put(f"key_{i}", f"value_{i:04d}")

    current_mem_2, peak_mem_2 = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Invariants:
    assert len(cache) <= max_items
    assert cache.stats().total_bytes <= max_bytes
    # Memory growth between warm-up and 100k inserts should be flat/minimal (< 1.5MB delta)
    delta_kb = (current_mem_2 - current_mem_1) / 1024.0
    assert delta_kb < 1500.0, f"Memory leaked excessively: delta {delta_kb:.1f} KB"
