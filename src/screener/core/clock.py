"""Injectable clock protocol and implementations for deterministic time maths.

Complexity: O(1) for all clock reads.
Thread-safety: wall_clock uses OS monotonic clock (thread-safe); FakeClock uses an internal Lock for advance/read.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

Clock = Callable[[], float]

wall_clock: Clock = time.monotonic


class FakeClock:
    """Deterministic simulated clock for testing rate limiters, TTL expiry, and timeouts."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._time: float = float(initial_time)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._time

    def now(self) -> float:
        with self._lock:
            return self._time

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError(f"Clock cannot move backwards: {seconds}")
        with self._lock:
            self._time += float(seconds)
            return self._time

    def set(self, target_time: float) -> None:
        with self._lock:
            if target_time < self._time:
                raise ValueError(f"Target time {target_time} is earlier than current time {self._time}")
            self._time = float(target_time)
