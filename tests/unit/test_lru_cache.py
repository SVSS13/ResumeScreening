import pytest
from screener.core.clock import FakeClock
from screener.core.lru_cache import LRUCache, OrderedDictLRUCache, SegmentedLRUCache


def test_lru_basic_get_put_pop():
    cache: LRUCache[str, int] = LRUCache(max_items=3)
    assert len(cache) == 0
    assert cache.get("a") is None

    cache.put("a", 10)
    cache.put("b", 20)
    assert len(cache) == 2
    assert cache.get("a") == 10
    assert cache.get("b") == 20

    popped = cache.pop("a")
    assert popped == 10
    assert cache.get("a") is None
    assert len(cache) == 1
    assert "b" in cache
    assert "a" not in cache


def test_lru_eviction_order():
    cache: LRUCache[str, str] = LRUCache(max_items=3)
    cache.put("k1", "v1")
    cache.put("k2", "v2")
    cache.put("k3", "v3")

    # Access k1 so recency becomes: k1 (MRU), k3, k2 (LRU)
    assert cache.get("k1") == "v1"

    # Insert k4 -> should evict k2
    cache.put("k4", "v4")
    assert cache.get("k2") is None
    assert cache.get("k1") == "v1"
    assert cache.get("k3") == "v3"
    assert cache.get("k4") == "v4"

    stats = cache.stats()
    assert stats.evictions == 1
    assert stats.hits == 4
    assert stats.misses == 1


def test_lru_update_existing_key():
    cache: LRUCache[str, str] = LRUCache(max_items=2)
    cache.put("a", "alpha")
    cache.put("b", "beta")
    # Update "a" -> moves to MRU, changes value
    cache.put("a", "ALPHA_NEW")

    # Insert "c" -> should evict "b"
    cache.put("c", "gamma")
    assert cache.get("a") == "ALPHA_NEW"
    assert cache.get("c") == "gamma"
    assert cache.get("b") is None


def test_lru_byte_bounded_eviction():
    # Value size: strings have sizeof >= 40 bytes
    cache: LRUCache[str, str] = LRUCache(max_items=100, max_bytes=200, sizeof=lambda s: len(s.encode()))
    cache.put("k1", "x" * 80)
    cache.put("k2", "y" * 80)
    assert len(cache) == 2
    assert cache.stats().total_bytes == 160

    # Inserting 80 bytes exceeds max_bytes (200), must evict k1
    cache.put("k3", "z" * 80)
    assert cache.get("k1") is None
    assert cache.get("k2") == "y" * 80
    assert cache.get("k3") == "z" * 80
    assert cache.stats().total_bytes == 160
    assert cache.stats().evictions == 1


def test_lru_evict_to():
    cache: LRUCache[str, str] = LRUCache(max_items=10, max_bytes=1000, sizeof=lambda s: len(s.encode()))
    for i in range(5):
        cache.put(f"k{i}", "v" * 100)
    assert cache.stats().total_bytes == 500

    # Evict down to 250 bytes -> should evict 3 oldest (k0, k1, k2)
    evicted = cache.evict_to(250)
    assert evicted == 3
    assert cache.stats().total_bytes == 200
    assert cache.get("k0") is None
    assert cache.get("k1") is None
    assert cache.get("k2") is None
    assert cache.get("k3") is not None
    assert cache.get("k4") is not None


def test_lru_lazy_ttl_expiry():
    clock = FakeClock(100.0)
    cache: LRUCache[str, str] = LRUCache(max_items=10, ttl_s=10.0, clock=clock)

    cache.put("a", "temp")
    assert cache.get("a") == "temp"

    # Advance clock by 5s -> still valid
    clock.advance(5.0)
    assert cache.get("a") == "temp"

    # Advance clock by another 6s (total 11s) -> expired
    clock.advance(6.0)
    assert cache.get("a") is None
    assert cache.stats().expirations == 1
    assert "a" not in cache


def test_lru_sweep_expired_min_heap():
    clock = FakeClock(100.0)
    cache: LRUCache[str, str] = LRUCache(max_items=10, clock=clock)

    cache.put("a", "val_a", ttl_s=5.0)   # exp: 105
    cache.put("b", "val_b", ttl_s=15.0)  # exp: 115
    cache.put("c", "val_c", ttl_s=25.0)  # exp: 125

    # Update "a" before expiration with new TTL (version bump)
    clock.advance(2.0)                    # now 102
    cache.put("a", "val_a_new", ttl_s=20.0)  # exp: 122 (stale heap entry at 105 must be skipped)

    # Advance to 110 -> old heap entry for "a" (105) is popped, but version check skips it!
    clock.advance(8.0)                    # now 110
    purged = cache.sweep_expired()
    assert purged == 0                    # nothing actually expired!
    assert cache.get("a") == "val_a_new"

    # Advance to 118 -> "b" should expire
    clock.advance(8.0)                    # now 118
    purged = cache.sweep_expired()
    assert purged == 1                    # "b" purged
    assert cache.get("b") is None
    assert cache.get("a") == "val_a_new"
    assert cache.get("c") == "val_c"


def test_ordered_dict_lru_cache_parity():
    clock = FakeClock(50.0)
    ref: OrderedDictLRUCache[str, int] = OrderedDictLRUCache(max_items=3, clock=clock)
    ref.put("x", 1)
    ref.put("y", 2)
    ref.put("z", 3)
    assert ref.get("x") == 1
    ref.put("w", 4)  # should evict y
    assert ref.get("y") is None
    assert ref.get("x") == 1
    assert ref.get("z") == 3
    assert ref.get("w") == 4
    assert ref.stats().evictions == 1


def test_segmented_lru_cache_scan_resistance():
    clock = FakeClock(10.0)
    # SLRU: 10 items total -> 2 probation, 8 protected
    slru: SegmentedLRUCache[str, str] = SegmentedLRUCache(max_items=10, probation_ratio=0.2, clock=clock)

    # Insert hot item "hot1" and access it again to promote to protected
    slru.put("hot1", "val1")
    assert slru.get("hot1") == "val1"  # Hit 2 -> Promoted to protected!

    # Insert another hot item "hot2" and promote
    slru.put("hot2", "val2")
    assert slru.get("hot2") == "val2"  # Promoted to protected!

    # Now simulate a batch scan of 20 ephemeral items (probation only has 2 items)
    for i in range(20):
        slru.put(f"scan_{i}", f"val_{i}")

    # The hot items must NOT be evicted by the scan!
    assert slru.get("hot1") == "val1"
    assert slru.get("hot2") == "val2"
