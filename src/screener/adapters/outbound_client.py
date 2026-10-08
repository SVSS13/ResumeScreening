"""Rate-limited and quota-aware HTTP client adapter for upstream integrations."""
from __future__ import annotations

from typing import Any, Mapping

import httpx

from ..core.rate_limiter import OutboundLimiter


class RateLimitedAsyncClient:
    """Wraps httpx.AsyncClient with OutboundLimiter for adaptive rate limiting."""

    def __init__(self, client: httpx.AsyncClient, limiter: OutboundLimiter) -> None:
        self._client = client
        self._limiter = limiter

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        await self._limiter.acquire()
        try:
            resp = await self._client.get(url, **kwargs)
            await self._limiter.observe(resp.headers, resp.status_code)
            return resp
        finally:
            self._limiter.release()

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        await self._limiter.acquire()
        try:
            resp = await self._client.post(url, **kwargs)
            await self._limiter.observe(resp.headers, resp.status_code)
            return resp
        finally:
            self._limiter.release()
