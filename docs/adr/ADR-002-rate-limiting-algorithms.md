# ADR-002: Inbound and Outbound Rate Limiting Algorithms

## Status
Accepted

## Context
The system faces two distinct rate limiting challenges:
1. **Inbound API Protection**: Protecting backend resources from client floods on `POST /screen` (heavy CPU/memory consumption) and metadata endpoints.
2. **Outbound Quota Management**: Protecting upstream integrations (GitHub API: 60 req/h unauthenticated or 5,000 req/h authenticated; LLM provider rate limits) from sudden bursts and ban responses.

Additionally, client rate limiter state must not create an unbounded memory leak when malicious clients query with randomized identifiers.

## Decision

### 1. Inbound Rate Limiting: Token Bucket as Default
We adopt the **Token Bucket** algorithm for default inbound rate limiting:
- State per client: `tokens: float`, `last_refill: float`.
- Capacity: $C$, Refill rate: $R$ tokens/second.
- **Lazy Refill on Request ($O(1)$)**:
  $$\Delta t = \text{now} - \text{last\_refill}$$
  $$\text{tokens} = \min(C, \text{tokens} + \Delta t \times R)$$
- If $\text{tokens} \ge \text{cost}$: deduct $\text{cost}$, allow request.
- Else: deny request, compute $\text{retry\_after} = \frac{\text{cost} - \text{tokens}}{R}$.
- **Burst Behavior**: Permits controlled short bursts up to bucket capacity $C$ while enforcing long-term rate $R$.

### 2. Strict Mode Option: Sliding-Window Counter
For routes requiring strict rate enforcement without token burst aggregation, we provide an optional **Sliding-Window Counter**:
- Maintains counts for the current fixed window and previous fixed window:
  $$\text{estimated\_count} = \text{count}_{\text{current}} + \text{count}_{\text{previous}} \times \left(1 - \frac{\text{time\_into\_current\_window}}{\text{window\_size}}\right)$$
- Provides smooth boundary limiting in $O(1)$ time and memory, avoiding the $2\times$ boundary surge of fixed windows and the high memory overhead of sliding log queues.

### 3. Traffic Shaping: Leaky Bucket Throttling vs. Limiting
- **Rate Limiting (Rejection)**: Excess requests immediately receive HTTP 429. Used for inbound edge protection against aggressive clients.
- **Throttling (Queue/Delay)**: Requests are queued in a bounded FIFO queue and delayed until bandwidth is available. If wait exceeds `max_wait_seconds`, requests are rejected with 429. Used for smoothing outbound upstream requests.

### 4. Memory-Bounded Client State: `KeyedLimiter` in LRU
To prevent client-ID denial-of-service (where an attacker sends unique keys to exhaust memory), per-key bucket states are stored inside a bounded `LRUCache` (`max_keys = 10,000`). Inactive client buckets are evicted at the tail in $O(1)$.

### 5. Adaptive Outbound Limiter for Upstreams
Outbound HTTP calls to GitHub and LLMs are governed by `OutboundLimiter`:
- Integrates `asyncio.Semaphore` (concurrency limit) with `TokenBucket` (rate limit).
- Inspects HTTP response headers: `x-ratelimit-remaining`, `x-ratelimit-reset`, and `Retry-After`.
- If `x-ratelimit-remaining == 0`, pauses token issuance until `x-ratelimit-reset`.
- On HTTP 429/503: applies truncated exponential backoff with full jitter:
  $$t_{\text{sleep}} = \text{uniform}(0, \min(t_{\text{max}}, t_{\text{base}} \times 2^{\text{attempt}}))$$

## Alternatives Considered & Rejected
- **Fixed Window Counter**: Rejected due to 2× burst vulnerability at window boundaries.
- **Sliding Window Log**: Rejected for inbound tracking because storing timestamps in a `deque` takes $O(\text{requests})$ memory per client.

## Consequences
- **Positive**: Low memory per client ($O(1)$ float pair); client-ID DoS immunity via LRU eviction; adaptive upstream quota safety.
- **Negative**: Microsecond clock dependency addressed by strictly injecting `time.monotonic()`.
