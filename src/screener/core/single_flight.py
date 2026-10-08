"""Single-flight coordinator for cache stampede (thundering-herd) suppression.

When multiple threads concurrently miss on the same key, SingleFlight coordinates
them so that only the first caller executes the upstream loader. All other concurrent
callers wait on the existing invocation and receive the identical result.

Complexity: O(1) coordination overhead.
Thread-safety: Fully thread-safe for both synchronous and asynchronous call paths.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable, Generic, TypeVar

V = TypeVar("V")


class _Call(Generic[V]):
    def __init__(self) -> None:
        self.event = threading.Event()
        self.val: V | None = None
        self.err: Exception | None = None


class SingleFlight(Generic[V]):
    """Synchronous SingleFlight coordinator using threading.Event."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, _Call[V]] = {}

    def execute(self, key: str, loader: Callable[[], V]) -> V:
        with self._lock:
            if key in self._calls:
                call = self._calls[key]
                first = False
            else:
                call = _Call[V]()
                self._calls[key] = call
                first = True

        if not first:
            call.event.wait()
            if call.err is not None:
                raise call.err
            assert call.val is not None
            return call.val

        # First caller: execute loader outside of any lock
        try:
            val = loader()
            call.val = val
            return val
        except Exception as e:
            call.err = e
            raise
        finally:
            call.event.set()
            with self._lock:
                self._calls.pop(key, None)


class AsyncSingleFlight(Generic[V]):
    """Asynchronous SingleFlight coordinator using asyncio.Future."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._calls: dict[str, asyncio.Future[V]] = {}

    async def execute(self, key: str, loader: Callable[[], Awaitable[V]]) -> V:
        async with self._lock:
            if key in self._calls:
                # Concurrent request: await existing in-flight future
                fut = self._calls[key]
                return await fut

            # First caller: create new future
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self._calls[key] = fut

        try:
            val = await loader()
            fut.set_result(val)
            return val
        except Exception as e:
            fut.set_exception(e)
            raise
        finally:
            async with self._lock:
                self._calls.pop(key, None)
