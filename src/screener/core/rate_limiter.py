"""Inbound and Outbound Rate Limiting and Traffic Shaping algorithms.

Components:
- TokenBucket: Burst-friendly token bucket with lazy refill (O(1)).
- SlidingWindowCounter: Two-window weighted approximation for strict rate limiting (O(1)).
- LeakyBucketThrottle: Traffic-shaping queue with configurable max wait before 429 rejection.
- KeyedLimiter: Per-client rate limiters stored in a bounded LRUCache to prevent DoS.
- OutboundLimiter: Adaptive async upstream limiter that tracks x-ratelimit-* and Retry-After headers.

Complexity: O(1) for all checks and token acquisitions.
Thread-safety: All classes are synchronized via threading.Lock or asyncio synchronization primitives.
"""
from __future__ import annotations

import asyncio
import collections
import dataclasses as dc
import math
import random
import threading
import time
from typing import Mapping

from .clock import Clock, wall_clock
from .lru_cache import LRUCache


@dc.dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_s: float
    remaining: float
    reset_time: float
    limit: float


class TokenBucket:
    """Thread-safe Token Bucket rate limiter with lazy O(1) refill."""

    def __init__(
        self,
        capacity: float,
        refill_per_s: float,
        clock: Clock = wall_clock,
    ) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        if refill_per_s <= 0:
            raise ValueError(f"refill_per_s must be > 0, got {refill_per_s}")

        self.capacity = float(capacity)
        self.refill_per_s = float(refill_per_s)
        self._clock = clock
        self._tokens = float(capacity)
        self._last_refill = clock()
        self._lock = threading.Lock()

    def try_acquire(self, cost: float = 1.0) -> RateDecision:
        now = self._clock()
        with self._lock:
            delta = max(0.0, now - self._last_refill)
            self._tokens = min(self.capacity, self._tokens + delta * self.refill_per_s)
            self._last_refill = now

            if self._tokens >= cost:
                self._tokens -= cost
                reset_time = now + ((self.capacity - self._tokens) / self.refill_per_s)
                return RateDecision(
                    allowed=True,
                    retry_after_s=0.0,
                    remaining=self._tokens,
                    reset_time=reset_time,
                    limit=self.capacity,
                )
            else:
                deficit = cost - self._tokens
                retry_after = deficit / self.refill_per_s
                reset_time = now + ((self.capacity - self._tokens) / self.refill_per_s)
                return RateDecision(
                    allowed=False,
                    retry_after_s=retry_after,
                    remaining=self._tokens,
                    reset_time=reset_time,
                    limit=self.capacity,
                )


class SlidingWindowCounter:
    """Thread-safe Sliding Window Counter using two fixed windows."""

    def __init__(
        self,
        limit: int,
        window_s: float = 60.0,
        clock: Clock = wall_clock,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._cur_window_start = clock()
        self._cur_count = 0
        self._prev_count = 0
        self._lock = threading.Lock()

    def try_acquire(self, cost: int = 1) -> RateDecision:
        now = self._clock()
        with self._lock:
            elapsed = now - self._cur_window_start
            if elapsed >= 2 * self.window_s:
                self._cur_window_start = now
                self._cur_count = 0
                self._prev_count = 0
                elapsed = 0.0
            elif elapsed >= self.window_s:
                self._prev_count = self._cur_count
                self._cur_count = 0
                self._cur_window_start += self.window_s
                elapsed = now - self._cur_window_start

            weight = max(0.0, 1.0 - (elapsed / self.window_s))
            estimated_count = self._cur_count + self._prev_count * weight

            if estimated_count + cost <= self.limit:
                self._cur_count += cost
                remaining = max(0.0, self.limit - (estimated_count + cost))
                reset_time = self._cur_window_start + self.window_s
                return RateDecision(
                    allowed=True,
                    retry_after_s=0.0,
                    remaining=remaining,
                    reset_time=reset_time,
                    limit=float(self.limit),
                )
            else:
                retry_after = max(0.1, self.window_s - elapsed)
                reset_time = self._cur_window_start + self.window_s
                return RateDecision(
                    allowed=False,
                    retry_after_s=retry_after,
                    remaining=0.0,
                    reset_time=reset_time,
                    limit=float(self.limit),
                )


class LeakyBucketThrottle:
    """Traffic shaping leaky bucket / FIFO queue with max wait before 429 rejection."""

    def __init__(
        self,
        rate_per_s: float,
        max_wait_s: float = 5.0,
        clock: Clock = wall_clock,
    ) -> None:
        self.rate_per_s = rate_per_s
        self.max_wait_s = max_wait_s
        self._clock = clock
        self._next_available_time = clock()
        self._lock = threading.Lock()

    def check(self) -> tuple[bool, float]:
        """Returns (allowed, wait_time_seconds). If wait_time > max_wait_s, allowed is False."""
        now = self._clock()
        with self._lock:
            earliest = max(now, self._next_available_time)
            wait = earliest - now
            if wait > self.max_wait_s:
                return False, wait
            self._next_available_time = earliest + (1.0 / self.rate_per_s)
            return True, wait


class KeyedLimiter:
    """Per-client rate limiter stored in a bounded LRU cache to prevent memory exhaustion DoS."""

    def __init__(
        self,
        capacity: float,
        refill_per_s: float,
        max_keys: int = 10_000,
        clock: Clock = wall_clock,
    ) -> None:
        self.capacity = capacity
        self.refill_per_s = refill_per_s
        self._clock = clock
        # Cache stores TokenBucket instances, evicts LRU clients when capacity is reached
        self._cache: LRUCache[str, TokenBucket] = LRUCache(
            max_items=max_keys,
            max_bytes=max_keys * 256,
            ttl_s=3600.0,  # Inactive client buckets expire in 1h
            clock=clock,
            sizeof=lambda _: 128,
        )
        self._lock = threading.Lock()

    def check(self, key: str, cost: float = 1.0) -> RateDecision:
        with self._lock:
            bucket = self._cache.get(key)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=self.capacity,
                    refill_per_s=self.refill_per_s,
                    clock=self._clock,
                )
                self._cache.put(key, bucket)
        return bucket.try_acquire(cost)


class OutboundLimiter:
    """Adaptive, async-friendly rate limiter for external upstream services (GitHub, LLMs).

    Combines concurrency limiting (Semaphore), rate limiting (TokenBucket), and response header inspection.
    """

    def __init__(
        self,
        max_concurrency: int = 5,
        rate_per_s: float = 2.0,
        burst: float = 5.0,
        clock: Clock = wall_clock,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)
        self._bucket = TokenBucket(capacity=burst, refill_per_s=rate_per_s, clock=clock)
        self._clock = clock
        self._paused_until: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        await self._sem.acquire()

        while True:
            now = self._clock()
            async with self._lock:
                wait_needed = self._paused_until - now
            if wait_needed <= 0:
                break
            await asyncio.sleep(min(1.0, wait_needed))

        decision = self._bucket.try_acquire(1.0)
        if not decision.allowed:
            await asyncio.sleep(decision.retry_after_s)

    def release(self) -> None:
        self._sem.release()

    async def observe(self, headers: Mapping[str, str], status_code: int) -> None:
        """Inspect upstream response headers to adaptively throttle future calls."""
        now = self._clock()
        remaining = headers.get("x-ratelimit-remaining")
        reset_ts_raw = headers.get("x-ratelimit-reset")
        retry_after_raw = headers.get("retry-after")

        if retry_after_raw is not None:
            try:
                retry_s = float(retry_after_raw)
                async with self._lock:
                    self._paused_until = max(self._paused_until, now + retry_s)
                return
            except ValueError:
                pass

        if status_code in (429, 403) and remaining == "0" and reset_ts_raw:
            try:
                # Unix timestamp reset (epoch seconds or mock clock)
                reset_ts = float(reset_ts_raw)
                base_time = time.time() if reset_ts > 1_000_000_000 else now
                delta_s = max(0.0, reset_ts - base_time)
                pause_s = min(3600.0, delta_s + 0.5)  # add 0.5s safety buffer, cap at 1 hour
                async with self._lock:
                    self._paused_until = max(self._paused_until, now + pause_s)
            except ValueError:
                async with self._lock:
                    self._paused_until = max(self._paused_until, now + 5.0)

        elif remaining is not None:
            try:
                rem_val = int(remaining)
                if rem_val <= 1:
                    # Nearing quota exhaustion, pause briefly
                    async with self._lock:
                        self._paused_until = max(self._paused_until, now + 2.0)
            except ValueError:
                pass
