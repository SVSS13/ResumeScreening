import concurrent.futures
import random
import time
import pytest

from screener.core.sharded_cache import ShardedLRUCache


def test_sharded_lru_concurrency_stress():
    """32 concurrent threads executing 100,000 total operations on ShardedLRUCache.

    Verifies deadlock freedom, no exceptions, and strict DLL + byte accounting invariants.
    """
    num_shards = 16
    max_items = 2_000
    cache: ShardedLRUCache[str, int] = ShardedLRUCache(
        num_shards=num_shards,
        max_items=max_items,
        max_bytes=500_000,
        sizeof=lambda _: 50,
    )

    num_threads = 32
    ops_per_thread = 3_125  # 32 * 3125 = 100,000 operations
    keys = [f"key_{i}" for i in range(500)]

    def worker_task(thread_id: int):
        rng = random.Random(thread_id)
        for _ in range(ops_per_thread):
            op = rng.random()
            key = rng.choice(keys)
            if op < 0.45:
                # 45% reads
                cache.get(key)
            elif op < 0.80:
                # 35% writes
                val = rng.randint(1, 1000)
                cache.put(key, val)
            elif op < 0.95:
                # 15% get_or_load
                cache.get_or_load(key, lambda: rng.randint(1001, 2000))
            else:
                # 5% pops
                cache.pop(key)

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_task, i) for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures, timeout=30.0):
            # Assert no unhandled exceptions occurred
            f.result()
    duration = time.perf_counter() - t0

    assert duration < 30.0, f"Concurrency test exceeded timeout (took {duration:.2f}s)"

    # Assert invariant consistency on every single shard
    for idx, shard in enumerate(cache._shards):
        with cache._locks[idx]:
            # 1. Map size equals DLL node count
            map_len = len(shard._map)
            # Count nodes by traversing DLL from head to tail
            dll_count = 0
            dll_bytes = 0
            cur = shard._head.next
            while cur is not None and cur is not shard._tail:
                dll_count += 1
                dll_bytes += cur.size_bytes
                cur = cur.next

            assert map_len == dll_count, f"Shard {idx}: map len {map_len} != DLL node count {dll_count}"
            assert shard._total_bytes == dll_bytes, (
                f"Shard {idx}: total_bytes {shard._total_bytes} != DLL sum {dll_bytes}"
            )
            assert map_len <= shard._max_items, f"Shard {idx} exceeded max_items: {map_len} > {shard._max_items}"
