"""FastAPI middleware enforcing per-client TokenBucket rate limiting."""
from __future__ import annotations

import math
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..core.metrics import MetricsCollector
from ..core.rate_limiter import KeyedLimiter, RateDecision


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces identity-based token bucket rate limiting on incoming HTTP requests."""

    def __init__(
        self,
        app: Callable,
        metrics: MetricsCollector | None = None,
        # Write route limit: 6 req/min, burst 2
        write_burst: float = 2.0,
        write_refill_per_s: float = 0.1,
        # Read route limit: 120 req/min, burst 20
        read_burst: float = 20.0,
        read_refill_per_s: float = 2.0,
    ) -> None:
        super().__init__(app)
        self.metrics = metrics
        self._write_limiter = KeyedLimiter(capacity=write_burst, refill_per_s=write_refill_per_s, max_keys=10_000)
        self._read_limiter = KeyedLimiter(capacity=read_burst, refill_per_s=read_refill_per_s, max_keys=10_000)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Extract identity: X-API-Key header or client host
        api_key = request.headers.get("X-API-Key")
        client_ip = request.client.host if request.client else "unknown"
        identity = f"key:{api_key}" if api_key else f"ip:{client_ip}"

        path = request.url.path
        method = request.method.upper()

        # Classify route
        if method == "POST" and path.startswith("/screen"):
            decision = self._write_limiter.check(identity, cost=1.0)
        else:
            decision = self._read_limiter.check(identity, cost=1.0)

        if not decision.allowed:
            if self.metrics:
                self.metrics.record_rate_limit()

            retry_after = max(1, math.ceil(decision.retry_after_s))
            headers = {
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(int(decision.limit)),
                "X-RateLimit-Remaining": str(int(decision.remaining)),
                "X-RateLimit-Reset": str(int(decision.reset_time)),
            }
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests", "retry_after": retry_after},
                headers=headers,
            )

        response: Response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(int(decision.limit))
        response.headers["X-RateLimit-Remaining"] = str(int(decision.remaining))
        response.headers["X-RateLimit-Reset"] = str(int(decision.reset_time))
        return response
