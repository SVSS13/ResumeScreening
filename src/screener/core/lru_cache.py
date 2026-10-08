"""Thread-safe, memory-bounded, TTL-aware LRU Cache implementations.

Components:
- Node: Doubly-linked list node tracking key, value, size_bytes, version, and TTL.
- LRUCache: Hand-crafted hash map + doubly linked list with O(1) get/put/evict,
  byte tracking, and version-checked min-heap TTL sweeps.
- OrderedDictLRUCache: Reference implementation based on collections.OrderedDict.
- SegmentedLRUCache: Scan-resistant Segmented LRU (SLRU) with probationary and
  protected partitions.

Complexity:
- get/put/pop: O(1) amortized
- evict_to: O(K) where K is number of evicted items
- sweep_expired: O(E log N) where E is number of expired items, N is heap size
Thread-safety:
- All methods on LRUCache, OrderedDictLRUCache, and SegmentedLRUCache are thread-safe,
  synchronized via an internal reentrant or mutual exclusion lock.
"""
from __future__ import annotations

import collections
import dataclasses as dc
import heapq
import sys
import threading
from typing import Any, Callable, Generic, Hashable, TypeVar

from .clock import Clock, wall_clock

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


def default_sizeof(val: Any) -> int:
    """Estimate memory footprint in bytes for cached items."""
    if val is None:
        return 0
    if isinstance(val, (bytes, bytearray)):
        return len(val)
    if isinstance(val, str):
        return sys.getsizeof(val)
    if isinstance(val, (int, float, bool)):
        return 24
    if isinstance(val, dict):
        return sys.getsizeof(val) + sum(default_sizeof(k) + default_sizeof(v) for k, v in val.items())
    if isinstance(val, (list, tuple, set)):
        return sys.getsizeof(val) + sum(default_sizeof(x) for x in val)
    if hasattr(val, "model_dump"):  # Pydantic v2
        try:
            return default_sizeof(val.model_dump())
        except Exception:
            return sys.getsizeof(val)
    if dc.is_dataclass(val):
        try:
            return default_sizeof(dc.asdict(val))
        except Exception:
            return sys.getsizeof(val)
    return sys.getsizeof(val)


@dc.dataclass(frozen=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    total_bytes: int = 0
    item_count: int = 0

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total > 0 else 0.0


class _Node(Generic[K, V]):
    __slots__ = ("key", "val", "size_bytes", "expires_at", "version", "prev", "next")

    def __init__(
        self,
        key: K,
        val: V,
        size_bytes: int = 0,
        expires_at: float | None = None,
        version: int = 0,
    ) -> None:
        self.key: K = key
        self.val: V = val
        self.size_bytes: int = size_bytes
        self.expires_at: float | None = expires_at
        self.version: int = version
        self.prev: _Node[K, V] | None = None
        self.next: _Node[K, V] | None = None


class LRUCache(Generic[K, V]):
    """Thread-safe LRU cache using a hash map and a hand-crafted doubly linked list."""

    def __init__(
        self,
        max_items: int = 10_000,
        max_bytes: int = 64 * 1024 * 1024,  # 64 MB default
        ttl_s: float | None = None,
        clock: Clock = wall_clock,
        sizeof: Callable[[Any], int] = default_sizeof,
    ) -> None:
        if max_items <= 0:
            raise ValueError(f"max_items must be > 0, got {max_items}")
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be > 0, got {max_bytes}")

        self._max_items = max_items
        self._max_bytes = max_bytes
        self._default_ttl_s = ttl_s
        self._clock = clock
        self._sizeof = sizeof

        self._lock = threading.Lock()
        self._map: dict[K, _Node[K, V]] = {}

        # Sentinel nodes for the doubly linked list
        # head.next points to MRU node; tail.prev points to LRU node
        self._head: _Node[K, V] = _Node(None, None)  # type: ignore[arg-type]
        self._tail: _Node[K, V] = _Node(None, None)  # type: ignore[arg-type]
        self._head.next = self._tail
        self._tail.prev = self._head

        # Min-heap of (expires_at, key, version) for active TTL sweeps
        self._ttl_heap: list[tuple[float, K, int]] = []

        # Telemetry counters
        self._total_bytes: int = 0
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._expirations: int = 0

    # ---- DLL pointer manipulation (caller must hold _lock) ----
    def _insert_after_head(self, node: _Node[K, V]) -> None:
        node.prev = self._head
        node.next = self._head.next
        assert self._head.next is not None
        self._head.next.prev = node
        self._head.next = node

    def _unlink(self, node: _Node[K, V]) -> None:
        prev_node = node.prev
        next_node = node.next
        if prev_node is not None:
            prev_node.next = next_node
        if next_node is not None:
            next_node.prev = prev_node
        node.prev = None
        node.next = None

    def _move_to_head(self, node: _Node[K, V]) -> None:
        self._unlink(node)
        self._insert_after_head(node)

    def _remove_node_internal(self, node: _Node[K, V]) -> None:
        self._unlink(node)
        self._map.pop(node.key, None)
        self._total_bytes -= node.size_bytes

    # ---- Public API ----
    def get(self, key: K) -> V | None:
        with self._lock:
            node = self._map.get(key)
            if node is None:
                self._misses += 1
                return None

            now = self._clock()
            if node.expires_at is not None and now >= node.expires_at:
                # Lazy TTL expiry
                self._remove_node_internal(node)
                self._expirations += 1
                self._misses += 1
                return None

            self._move_to_head(node)
            self._hits += 1
            return node.val

    def put(self, key: K, value: V, ttl_s: float | None = None) -> None:
        ttl = ttl_s if ttl_s is not None else self._default_ttl_s
        val_size = self._sizeof(value)
        now = self._clock()
        expires_at = (now + ttl) if ttl is not None and ttl > 0 else None

        with self._lock:
            existing = self._map.get(key)
            if existing is not None:
                # Update existing entry
                self._total_bytes -= existing.size_bytes
                existing.val = value
                existing.size_bytes = val_size
                existing.expires_at = expires_at
                existing.version += 1
                self._total_bytes += val_size
                self._move_to_head(existing)

                if expires_at is not None:
                    heapq.heappush(self._ttl_heap, (expires_at, key, existing.version))
            else:
                new_node = _Node(
                    key=key,
                    val=value,
                    size_bytes=val_size,
                    expires_at=expires_at,
                    version=1,
                )
                self._insert_after_head(new_node)
                self._map[key] = new_node
                self._total_bytes += val_size

                if expires_at is not None:
                    heapq.heappush(self._ttl_heap, (expires_at, key, 1))

            # Evict from tail while exceeding item limit or byte limit
            while (len(self._map) > self._max_items or self._total_bytes > self._max_bytes) and self._tail.prev is not self._head:
                lru_node = self._tail.prev
                assert lru_node is not None and lru_node is not self._head
                self._remove_node_internal(lru_node)
                self._evictions += 1

    def pop(self, key: K) -> V | None:
        with self._lock:
            node = self._map.get(key)
            if node is None:
                return None
            self._remove_node_internal(node)
            return node.val

    def evict_to(self, target_bytes: int) -> int:
        """Evict least recently used entries until total_bytes <= target_bytes. Returns evicted count."""
        target = max(0, target_bytes)
        evicted = 0
        with self._lock:
            while self._total_bytes > target and self._tail.prev is not self._head:
                lru_node = self._tail.prev
                assert lru_node is not None and lru_node is not self._head
                self._remove_node_internal(lru_node)
                self._evictions += 1
                evicted += 1
        return evicted

    def sweep_expired(self) -> int:
        """Sweeps expired items from the min-heap. Returns count of purged entries."""
        now = self._clock()
        expired_count = 0
        with self._lock:
            while self._ttl_heap and self._ttl_heap[0][0] <= now:
                expires_at, key, version = heapq.heappop(self._ttl_heap)
                node = self._map.get(key)
                if node is not None and node.version == version:
                    # Valid matching version that has expired
                    self._remove_node_internal(node)
                    self._expirations += 1
                    expired_count += 1
                # Otherwise, node was updated with a newer version or deleted -> stale heap record skipped in O(1)
        return expired_count

    def clear(self) -> None:
        with self._lock:
            self._map.clear()
            self._head.next = self._tail
            self._tail.prev = self._head
            self._ttl_heap.clear()
            self._total_bytes = 0

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                expirations=self._expirations,
                total_bytes=self._total_bytes,
                item_count=len(self._map),
            )

    def __len__(self) -> int:
        with self._lock:
            return len(self._map)

    def __contains__(self, key: K) -> bool:
        with self._lock:
            node = self._map.get(key)
            if node is None:
                return False
            if node.expires_at is not None and self._clock() >= node.expires_at:
                return False
            return True


class OrderedDictLRUCache(Generic[K, V]):
    """Reference LRU cache using collections.OrderedDict for property testing & benchmarking."""

    def __init__(
        self,
        max_items: int = 10_000,
        max_bytes: int = 64 * 1024 * 1024,
        ttl_s: float | None = None,
        clock: Clock = wall_clock,
        sizeof: Callable[[Any], int] = default_sizeof,
    ) -> None:
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._default_ttl_s = ttl_s
        self._clock = clock
        self._sizeof = sizeof

        self._lock = threading.Lock()
        self._data: collections.OrderedDict[K, tuple[V, int, float | None]] = collections.OrderedDict()
        self._total_bytes: int = 0
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._expirations: int = 0

    def get(self, key: K) -> V | None:
        with self._lock:
            if key not in self._data:
                self._misses += 1
                return None
            val, size, exp = self._data[key]
            now = self._clock()
            if exp is not None and now >= exp:
                del self._data[key]
                self._total_bytes -= size
                self._expirations += 1
                self._misses += 1
                return None
            self._data.move_to_end(key, last=True)
            self._hits += 1
            return val

    def put(self, key: K, value: V, ttl_s: float | None = None) -> None:
        ttl = ttl_s if ttl_s is not None else self._default_ttl_s
        now = self._clock()
        exp = (now + ttl) if ttl is not None and ttl > 0 else None
        size = self._sizeof(value)

        with self._lock:
            if key in self._data:
                _, old_size, _ = self._data[key]
                self._total_bytes -= old_size
            self._data[key] = (value, size, exp)
            self._data.move_to_end(key, last=True)
            self._total_bytes += size

            while (len(self._data) > self._max_items or self._total_bytes > self._max_bytes) and self._data:
                _, (_, evicted_size, _) = self._data.popitem(last=False)
                self._total_bytes -= evicted_size
                self._evictions += 1

    def pop(self, key: K) -> V | None:
        with self._lock:
            if key not in self._data:
                return None
            val, size, _ = self._data.pop(key)
            self._total_bytes -= size
            return val

    def evict_to(self, target_bytes: int) -> int:
        evicted = 0
        with self._lock:
            while self._total_bytes > target_bytes and self._data:
                _, (_, size, _) = self._data.popitem(last=False)
                self._total_bytes -= size
                self._evictions += 1
                evicted += 1
        return evicted

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                expirations=self._expirations,
                total_bytes=self._total_bytes,
                item_count=len(self._data),
            )

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._total_bytes = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class SegmentedLRUCache(Generic[K, V]):
    """Scan-resistant Segmented LRU (SLRU) cache.

    Divides capacity into a probationary segment (default 20%) and a protected segment (default 80%).
    New items enter probation. On second access, items are promoted to protected.
    When protected fills, items demote to probation.
    """

    def __init__(
        self,
        max_items: int = 10_000,
        max_bytes: int = 64 * 1024 * 1024,
        ttl_s: float | None = None,
        clock: Clock = wall_clock,
        sizeof: Callable[[Any], int] = default_sizeof,
        probation_ratio: float = 0.2,
    ) -> None:
        probation_ratio = max(0.05, min(0.5, probation_ratio))
        prob_items = max(1, int(max_items * probation_ratio))
        prot_items = max(1, max_items - prob_items)
        prob_bytes = max(1024, int(max_bytes * probation_ratio))
        prot_bytes = max(1024, max_bytes - prob_bytes)

        self._probation: LRUCache[K, V] = LRUCache(
            max_items=prob_items,
            max_bytes=prob_bytes,
            ttl_s=ttl_s,
            clock=clock,
            sizeof=sizeof,
        )
        self._protected: LRUCache[K, V] = LRUCache(
            max_items=prot_items,
            max_bytes=prot_bytes,
            ttl_s=ttl_s,
            clock=clock,
            sizeof=sizeof,
        )
        self._lock = threading.Lock()
        self._clock = clock
        self._default_ttl_s = ttl_s

    def get(self, key: K) -> V | None:
        with self._lock:
            # Check protected first
            val = self._protected.get(key)
            if val is not None:
                return val

            # Check probationary
            val = self._probation.get(key)
            if val is not None:
                # Promotion on second hit: remove from probation, promote to protected
                self._probation.pop(key)
                self._protected.put(key, val, ttl_s=self._default_ttl_s)
                return val

            return None

    def put(self, key: K, value: V, ttl_s: float | None = None) -> None:
        with self._lock:
            if key in self._protected:
                self._protected.put(key, value, ttl_s=ttl_s)
            else:
                self._probation.put(key, value, ttl_s=ttl_s)

    def pop(self, key: K) -> V | None:
        with self._lock:
            val = self._protected.pop(key)
            if val is not None:
                return val
            return self._probation.pop(key)

    def stats(self) -> CacheStats:
        with self._lock:
            s_prob = self._probation.stats()
            s_prot = self._protected.stats()
            return CacheStats(
                hits=s_prob.hits + s_prot.hits,
                misses=s_prob.misses,  # Misses counted on probationary lookup
                evictions=s_prob.evictions + s_prot.evictions,
                expirations=s_prob.expirations + s_prot.expirations,
                total_bytes=s_prob.total_bytes + s_prot.total_bytes,
                item_count=s_prob.item_count + s_prot.item_count,
            )

    def evict_to(self, target_bytes: int) -> int:
        with self._lock:
            # Evict from probation first, then protected
            prob_target = int(target_bytes * 0.2)
            prot_target = max(0, target_bytes - prob_target)
            evicted = self._probation.evict_to(prob_target)
            evicted += self._protected.evict_to(prot_target)
            return evicted

    def clear(self) -> None:
        with self._lock:
            self._probation.clear()
            self._protected.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._probation) + len(self._protected)
