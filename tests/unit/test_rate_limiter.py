import pytest
from screener.core.clock import FakeClock
from screener.core.rate_limiter import (
    KeyedLimiter,
    LeakyBucketThrottle,
    SlidingWindowCounter,
    TokenBucket,
)


def test_token_bucket_initial_capacity_and_refill():
    clock = FakeClock(100.0)
    # Cap = 5, Refill = 1 token/sec
    tb = TokenBucket(capacity=5.0, refill_per_s=1.0, clock=clock)

    # Burst 5 requests immediately -> all allowed
    for _ in range(5):
        decision = tb.try_acquire(1.0)
        assert decision.allowed
        assert decision.retry_after_s == 0.0

    # 6th request immediately -> rejected
    decision = tb.try_acquire(1.0)
    assert not decision.allowed
    assert 0.99 <= decision.retry_after_s <= 1.01

    # Advance clock by 0.5s -> deficit is 0.5s
    clock.advance(0.5)
    decision = tb.try_acquire(1.0)
    assert not decision.allowed
    assert 0.49 <= decision.retry_after_s <= 0.51

    # Advance clock by 0.5s (total 1.0s elapsed) -> allowed!
    clock.advance(0.5)
    decision = tb.try_acquire(1.0)
    assert decision.allowed
    assert decision.retry_after_s == 0.0


def test_token_bucket_caps_at_maximum_capacity():
    clock = FakeClock(10.0)
    tb = TokenBucket(capacity=10.0, refill_per_s=2.0, clock=clock)

    # Advance clock by 100 seconds
    clock.advance(100.0)
    decision = tb.try_acquire(1.0)
    assert decision.allowed
    # Remaining should be capped at (capacity - 1) = 9.0
    assert decision.remaining == 9.0


def test_sliding_window_counter():
    clock = FakeClock(100.0)
    # 5 requests per 60 seconds
    sw = SlidingWindowCounter(limit=5, window_s=60.0, clock=clock)

    for _ in range(5):
        assert sw.try_acquire(1).allowed

    # 6th should be denied
    assert not sw.try_acquire(1).allowed

    # Advance 30s into the next window (total 90s elapsed)
    clock.advance(90.0)
    # Weight of previous window is (1 - 30/60) = 0.5 -> estimated = 5 * 0.5 = 2.5
    # 2.5 + 1 <= 5 -> allowed!
    assert sw.try_acquire(1).allowed


def test_leaky_bucket_throttle():
    clock = FakeClock(10.0)
    # 2 req/s, max wait 2.0s
    throttle = LeakyBucketThrottle(rate_per_s=2.0, max_wait_s=2.0, clock=clock)

    # 1st request -> immediate (wait = 0)
    allowed, wait = throttle.check()
    assert allowed and wait == 0.0

    # 2nd request -> wait = 0.5s
    allowed, wait = throttle.check()
    assert allowed and wait == 0.5

    # 3rd request -> wait = 1.0s
    allowed, wait = throttle.check()
    assert allowed and wait == 1.0

    # 4th request -> wait = 1.5s
    allowed, wait = throttle.check()
    assert allowed and wait == 1.5

    # 5th request -> wait = 2.0s
    allowed, wait = throttle.check()
    assert allowed and wait == 2.0

    # 6th request -> wait = 2.5s > max_wait_s (2.0) -> rejected!
    allowed, wait = throttle.check()
    assert not allowed
    assert wait == 2.5


def test_keyed_limiter_lru_client_bounds():
    clock = FakeClock(100.0)
    # Bounded to 3 clients
    limiter = KeyedLimiter(capacity=2.0, refill_per_s=1.0, max_keys=3, clock=clock)

    # Client A, B, C consume tokens
    assert limiter.check("client_a").allowed
    assert limiter.check("client_b").allowed
    assert limiter.check("client_c").allowed

    # Access Client A again so order is: A (MRU), C, B (LRU)
    assert limiter.check("client_a").allowed

    # Client D arrives -> B should be evicted as LRU
    assert limiter.check("client_d").allowed

    assert len(limiter._cache) == 3
    assert "client_a" in limiter._cache
    assert "client_c" in limiter._cache
    assert "client_d" in limiter._cache
    assert "client_b" not in limiter._cache
