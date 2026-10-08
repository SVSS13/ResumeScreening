"""Lock-striped sharded LRU cache with Single-Flight stampede suppression.

Design:
- Keys are partitioned across N independent shards using hash(key) % N.
- Each shard contains an independent LRUCache instance and an independent threading.Lock.
- Threads accessing keys in different shards run completely in parallel with zero lock contention.
- Lock-Ordering Rule: A thread holds at most ONE shard lock at any instant. Cross-shard operations
  acquire and release shard locks strictly sequentially, guaranteeing deadlock freedom.
- No locks are ever held while executing external loaders or network I/O.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Generic, Hashable, TypeVar

from .clock import Clock, wall_clock
from .lru_cache import CacheStats, LRUCache, default_sizeof
from .single_flight import SingleFlight

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class ShardedLRUCache(Generic[K, V]):
    """Lock-striped concurrent LRU cache partitioned into N independent shards."""

    def __init__(
        self,
        num_shards: int = 16,
        max_items: int = 10_000,
        max_bytes: int = 64 * 1024 * 1024,
        ttl_s: float | None = None,
        clock: Clock = wall_clock,
        sizeof: Callable[[Any], int] = default_sizeof,
    ) -> None:
        if num_shards <= 0 or (num_shards & (num_shards - 1)) != 0:
            # Recommend powers of 2 for uniform bitwise distribution
            pass
        self._num_shards = max(1, num_shards)
        shard_items = max(1, max_items // self._num_shards)
        shard_bytes = max(1024, max_bytes // self._num_shards)

        self._shards: list[LRUCache[K, V]] = [
            LRUCache(
                max_items=shard_items,
                max_bytes=shard_bytes,
                ttl_s=ttl_s,
                clock=clock,
                sizeof=sizeof,
            )
            for _ in range(self._num_shards)
        ]
        self._locks: list[threading.Lock] = [threading.Lock() for _ in range(self._num_shards)]
        self._single_flight: SingleFlight[V] = SingleFlight[V]()
        self._clock = clock
        self._default_ttl_s = ttl_s

        # Background TTL sweeper thread
        self._sweeper_thread: threading.Thread | None = None
        self._stop_sweeper = threading.Event()

    def _shard_index(self, key: K) -> int:
        return abs(hash(key)) % self._num_shards

    def get(self, key: K) -> V | None:
        idx = self._shard_index(key)
        with self._locks[idx]:
            return self._shards[idx].get(key)

    def put(self, key: K, value: V, ttl_s: float | None = None) -> None:
        idx = self._shard_index(key)
        with self._locks[idx]:
            self._shards[idx].put(key, value, ttl_s=ttl_s)

    def pop(self, key: K) -> V | None:
        idx = self._shard_index(key)
        with self._locks[idx]:
            return self._shards[idx].pop(key)

    def get_or_load(self, key: K, loader: Callable[[], V], ttl_s: float | None = None) -> V:
        """Single-flight cache lookup: returns cached value or loads it exactly once among concurrent callers.

        CRITICAL CONCURRENCY RULE: No shard lock is held while invoking loader()!
        """
        idx = self._shard_index(key)
        with self._locks[idx]:
            val = self._shards[idx].get(key)
            if val is not None:
                return val

        def _load_and_cache() -> V:
            loaded_val = loader()
            with self._locks[idx]:
                self._shards[idx].put(key, loaded_val, ttl_s=ttl_s)
            return loaded_val

        return self._single_flight.execute(str(key), _load_and_cache)

    def evict_to(self, target_bytes: int) -> int:
        """Evict oldest entries across all shards until sum(total_bytes) <= target_bytes.

        Acquires shard locks strictly sequentially to preserve lock ordering.
        """
        target_per_shard = max(0, target_bytes // self._num_shards)
        total_evicted = 0
        for i in range(self._num_shards):
            with self._locks[i]:
                total_evicted += self._shards[i].evict_to(target_per_shard)
        return total_evicted

    def sweep_expired(self) -> int:
        """Sweep expired entries across all shards sequentially."""
        total_expired = 0
        for i in range(self._num_shards):
            with self._locks[i]:
                total_expired += self._shards[i].sweep_expired()
        return total_expired

    def clear(self) -> None:
        for i in range(self._num_shards):
            with self._locks[i]:
                self._shards[i].clear()

    def stats(self) -> CacheStats:
        """Aggregate stats across all shards sequentially."""
        hits = misses = evictions = expirations = total_bytes = item_count = 0
        for i in range(self._num_shards):
            with self._locks[i]:
                s = self._shards[i].stats()
                hits += s.hits
                misses += s.misses
                evictions += s.evictions
                expirations += s.expirations
                total_bytes += s.total_bytes
                item_count += s.item_count
        return CacheStats(
            hits=hits,
            misses=misses,
            evictions=evictions,
            expirations=expirations,
            total_bytes=total_bytes,
            item_count=item_count,
        )

    def __len__(self) -> int:
        count = 0
        for i in range(self._num_shards):
            with self._locks[i]:
                count += len(self._shards[i])
        return count

    def __contains__(self, key: K) -> bool:
        idx = self._shard_index(key)
        with self._locks[idx]:
            return key in self._shards[idx]

    def start_sweeper(self, interval_s: float = 10.0) -> None:
        if self._sweeper_thread is not None and self._sweeper_thread.is_alive():
            return
        self._stop_sweeper.clear()

        def _sweep_loop() -> None:
            while not self._stop_sweeper.wait(interval_s):
                self.sweep_expired()

        self._sweeper_thread = threading.Thread(target=_sweep_loop, daemon=True, name="ShardedCacheSweeper")
        self._sweeper_thread.start()

    def stop_sweeper(self) -> None:
        self._stop_sweeper.set()
        if self._sweeper_thread is not None:
            self._sweeper_thread.join(timeout=1.0)
            self._sweeper_thread = None
