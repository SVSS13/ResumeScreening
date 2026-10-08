import asyncio
import pytest
from screener.core.clock import FakeClock
from screener.core.rate_limiter import OutboundLimiter


def test_outbound_limiter_observes_retry_after():
    clock = FakeClock(100.0)
    limiter = OutboundLimiter(max_concurrency=2, rate_per_s=10.0, burst=10.0, clock=clock)

    # Initial acquire succeeds
    async def run():
        await limiter.acquire()
        limiter.release()

        # Simulate 429 response with Retry-After: 3
        await limiter.observe({"retry-after": "3.0"}, 429)

        # paused_until should be at least now + 3 = 103.0
        assert limiter._paused_until >= 103.0

        # Advance clock to 104.0
        clock.advance(4.0)

        # Subsequent acquire succeeds immediately
        await limiter.acquire()
        limiter.release()

    asyncio.run(run())


def test_outbound_limiter_observes_github_exhaustion():
    clock = FakeClock(100.0)
    limiter = OutboundLimiter(max_concurrency=2, rate_per_s=10.0, burst=10.0, clock=clock)

    async def run():
        # GitHub quota 0, reset at 110.0
        await limiter.observe({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "110"}, 403)
        assert limiter._paused_until >= 105.0

    asyncio.run(run())
