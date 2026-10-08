import time
import pytest

from screener.core.clock import FakeClock
from screener.core.lru_cache import LRUCache, OrderedDictLRUCache, SegmentedLRUCache, default_sizeof
from screener.core.memory_guard import MemoryGuard
from screener.core.metrics import MetricsCollector
from screener.core.rate_limiter import OutboundLimiter, SlidingWindowCounter, TokenBucket
from screener.core.sharded_cache import ShardedLRUCache
from screener.core.single_flight import AsyncSingleFlight, SingleFlight


def test_default_sizeof_various_types():
    assert default_sizeof(None) == 0
    assert default_sizeof(b"bytes") == 5
    assert default_sizeof("string") > 0
    assert default_sizeof(123) == 24
    assert default_sizeof(True) == 24
    assert default_sizeof({"key": "val"}) > 0
    assert default_sizeof([1, 2, 3]) > 0
    assert default_sizeof((1, 2)) > 0
    assert default_sizeof({1, 2}) > 0


def test_lru_cache_clear_and_validation():
    with pytest.raises(ValueError):
        LRUCache(max_items=0)
    with pytest.raises(ValueError):
        LRUCache(max_bytes=-1)

    cache: LRUCache[str, str] = LRUCache(max_items=5)
    cache.put("a", "1")
    cache.put("b", "2")
    assert len(cache) == 2
    cache.clear()
    assert len(cache) == 0
    assert cache.get("a") is None


def test_ordered_dict_lru_clear():
    cache: OrderedDictLRUCache[str, str] = OrderedDictLRUCache(max_items=5)
    cache.put("a", "1")
    cache.clear()
    assert len(cache) == 0


def test_segmented_lru_extra_methods():
    clock = FakeClock(100.0)
    slru: SegmentedLRUCache[str, str] = SegmentedLRUCache(max_items=10, clock=clock)
    slru.put("a", "1")
    slru.put("b", "2")
    assert len(slru) == 2

    # pop
    assert slru.pop("a") == "1"
    assert slru.pop("nonexistent") is None

    # stats
    stats = slru.stats()
    assert stats.item_count == 1

    # evict_to
    slru.put("c", "3")
    slru.evict_to(0)
    assert len(slru) == 0

    # clear
    slru.put("x", "val")
    slru.clear()
    assert len(slru) == 0


def test_sharded_lru_methods():
    clock = FakeClock(100.0)
    cache: ShardedLRUCache[str, str] = ShardedLRUCache(num_shards=4, max_items=20, clock=clock)

    cache.put("k1", "v1")
    cache.put("k2", "v2")
    assert "k1" in cache
    assert "k99" not in cache
    assert len(cache) == 2

    # Stats
    s = cache.stats()
    assert s.item_count == 2
    assert s.hits == 0

    # Evict to
    cache.evict_to(0)
    assert len(cache) == 0

    # Sweeper thread start & stop
    cache.start_sweeper(interval_s=0.1)
    time.sleep(0.05)
    cache.stop_sweeper()

    # Clear
    cache.put("x", "y")
    cache.clear()
    assert len(cache) == 0


def test_memory_guard_lifecycle():
    guard = MemoryGuard(cache_max_bytes=1000, check_interval_s=0.1)
    cache: LRUCache[str, str] = LRUCache(max_items=10)
    guard.register_cache(cache)
    guard.unregister_cache(cache)

    guard.start()
    time.sleep(0.05)
    guard.stop()


def test_sliding_window_counter_double_window_skip():
    clock = FakeClock(100.0)
    sw = SlidingWindowCounter(limit=5, window_s=10.0, clock=clock)
    sw.try_acquire(1)

    # Advance beyond 2x window_s
    clock.advance(25.0)
    decision = sw.try_acquire(1)
    assert decision.allowed


def test_outbound_limiter_more_branches():
    import asyncio
    clock = FakeClock(100.0)
    limiter = OutboundLimiter(max_concurrency=2, rate_per_s=10.0, clock=clock)

    async def run():
        # Invalid retry-after string
        await limiter.observe({"retry-after": "invalid"}, 429)

        # Status 429 with invalid reset header
        await limiter.observe({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "invalid"}, 429)

        # Status 200 with remaining <= 1
        await limiter.observe({"x-ratelimit-remaining": "1"}, 200)

        # Status 200 with invalid remaining
        await limiter.observe({"x-ratelimit-remaining": "invalid"}, 200)

    asyncio.run(run())


def test_single_flight_exceptions():
    import asyncio
    sf = SingleFlight[str]()

    def failing_loader():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        sf.execute("err_key", failing_loader)

    # Async SingleFlight exception
    asf = AsyncSingleFlight[str]()

    async def async_failing_loader():
        raise ValueError("async_boom")

    async def run():
        with pytest.raises(ValueError):
            await asf.execute("async_err_key", async_failing_loader)

    asyncio.run(run())


def test_metrics_collector_throttle():
    collector = MetricsCollector()
    collector.record_outbound_throttle()
    s = collector.snapshot()
    assert s["outbound_throttled_total"] == 1
