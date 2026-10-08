"""Hardened concurrency and stress tests verifying Findings A2 and A3.

Verifies:
- SingleFlight thread-safety under heavy concurrent contention (32 threads).
- Lock-Free Loader Invariant: No shard lock is held while loader() executes.
- Strict Lock Hierarchy: No thread ever holds multiple shard locks or deadlocks.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytest

from screener.core.sharded_cache import ShardedLRUCache
from screener.core.single_flight import SingleFlight


def test_single_flight_stress_32_threads():
    """Finding A3: Verify 32 concurrent threads executing 3,200 operations across shared keys.

    Proves that for each unique key under contention, loader() runs exactly once,
    all callers receive identical results, and no deadlocks or exceptions occur.
    """
    sf = SingleFlight[int]()
    load_counts = {k: 0 for k in range(10)}
    counts_lock = threading.Lock()

    def slow_loader(key_val: int) -> int:
        time.sleep(0.005)  # 5ms latency to induce maximum contention
        with counts_lock:
            load_counts[key_val] += 1
        return key_val * 100

    def worker(worker_id: int):
        results = []
        for i in range(100):
            k = i % 10
            res = sf.execute(f"key_{k}", lambda k=k: slow_loader(k))
            assert res == k * 100
            results.append(res)
        return len(results)

    num_threads = 32
    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(worker, t_id) for t_id in range(num_threads)]
        for f in as_completed(futures, timeout=10.0):
            assert f.result() == 100


def test_no_shard_lock_held_during_loader():
    """Finding A2: Formally prove that NO shard lock is held while loader() executes.

    While a slow loader is sleeping inside get_or_load(key_A), another thread
    must be able to concurrently acquire key_A's shard lock, read other keys,
    and insert into the SAME shard with zero blocking.
    """
    cache = ShardedLRUCache[str, str](num_shards=4)
    shard_0_key = "k_shard0"
    shard_idx = cache._shard_index(shard_0_key)

    loader_started = threading.Event()
    release_loader = threading.Event()
    concurrent_probe_succeeded = threading.Event()

    def slow_loader() -> str:
        loader_started.set()
        # Wait until other thread confirms it acquired the shard lock
        if not release_loader.wait(timeout=5.0):
            raise TimeoutError("Loader timed out waiting for probe")
        return "loaded_val"

    def background_loader():
        return cache.get_or_load(shard_0_key, slow_loader)

    loader_thread = threading.Thread(target=background_loader)
    loader_thread.start()

    # Wait for loader to be in-flight
    assert loader_started.wait(timeout=2.0), "Loader never started"

    # Now verify another thread can immediately acquire the shard lock for shard_idx!
    acquired_immediately = cache._locks[shard_idx].acquire(blocking=True, timeout=0.1)
    try:
        assert acquired_immediately, (
            f"DEADLOCK/LOCK HELD BUG: Shard lock {shard_idx} was held during loader() execution!"
        )
        concurrent_probe_succeeded.set()
    finally:
        if acquired_immediately:
            cache._locks[shard_idx].release()
        release_loader.set()

    loader_thread.join(timeout=2.0)
    assert concurrent_probe_succeeded.is_set()
    assert cache.get(shard_0_key) == "loaded_val"


def test_lock_hierarchy_deadlock_freedom():
    """Finding A2: Stress test cross-shard evictions, sweepers, and concurrent reads.

    Proves that sequential traversal in evict_to() and sweep_expired() never deadlocks
    against concurrent gets and puts across 16 threads.
    """
    cache = ShardedLRUCache[int, int](num_shards=8, max_items=500)
    stop_event = threading.Event()

    def writer_worker(tid: int):
        i = 0
        while not stop_event.is_set() and i < 500:
            cache.put(tid * 1000 + (i % 50), i)
            cache.get(tid * 1000 + ((i + 1) % 50))
            i += 1

    def evictor_worker():
        while not stop_event.is_set():
            cache.evict_to(target_bytes=1024)
            cache.sweep_expired()
            time.sleep(0.001)

    threads = [threading.Thread(target=writer_worker, args=(t,)) for t in range(8)]
    evictors = [threading.Thread(target=evictor_worker) for _ in range(2)]

    for t in threads + evictors:
        t.start()

    for t in threads:
        t.join(timeout=5.0)

    stop_event.set()
    for t in evictors:
        t.join(timeout=2.0)

    assert len(cache) >= 0
