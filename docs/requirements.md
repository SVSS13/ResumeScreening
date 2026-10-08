# Phase 1: Requirements & Analysis Document
## AI Resume Screening & Ranking System Backend

### 1. Context and Problem Statement
The existing resume screening backend in Python 3.12 provides deterministic eligibility filtering, 100-point rubric scoring, and optional GitHub/LLM enrichment for resumes in PDF, DOCX, and TXT formats.
However, it currently exhibits several critical architectural limitations:
1. **Unbounded, Ad-Hoc Caches**: GitHub repository responses are held in an un-evicted JSON file and in-memory `dict` lacking TTL, memory bounds, thread safety, or hit/miss instrumentation.
2. **Synchronous Blocking Endpoints**: `POST /screen` blocks worker threads synchronously during the full execution of the pipeline (~15 seconds for 50 files). It lacks authentication, identity-aware rate limiting, job tracking, and backpressure.
3. **Upstream Quota Vulnerability**: Outbound GitHub and LLM API requests use a concurrency semaphore but ignore HTTP rate limit headers (`x-ratelimit-remaining`, `x-ratelimit-reset`, `Retry-After`), risking HTTP 403 and 429 quota exhaustion.
4. **Underutilized Multicore Hardware**: PDF text parsing and TF-IDF matrix computations are CPU-bound and execute sequentially in a single process. Running `asyncio.run()` inside a synchronous function hampers clean async serving.
5. **Lack of Resource Controls and Observability**: No hard memory ceilings or runtime watermark eviction exist, risking out-of-memory (OOM) failures under heavy load. There is no graceful shutdown protocol or `/metrics` instrumentation.

---

### 2. Functional Requirements (FR)

- **FR-1: Inbound Identity-Based Rate Limiting & Throttling**
  - **FR-1.1**: The system must authenticate clients via an `X-API-Key` request header; unauthenticated requests fall back to client IP identity.
  - **FR-1.2**: Distinct rate limit policies must apply to endpoint classes:
    - *Read/Query Endpoints* (`GET /jobs/{id}`, `GET /results/{id}`, `GET /health`, `GET /metrics`): Default 120 req/min with burst capacity of 20.
    - *Heavy Mutation/Compute Endpoints* (`POST /screen`): Default 6 req/min with burst capacity of 2.
  - **FR-1.3**: When a client exceeds their quota, the system must return HTTP 429 Too Many Requests with headers:
    - `Retry-After`: Seconds until quota allows the request.
    - `X-RateLimit-Limit`: Maximum burst capacity for the window.
    - `X-RateLimit-Remaining`: Current available tokens in bucket.
    - `X-RateLimit-Reset`: Unix timestamp when the bucket fully resets.
  - **FR-1.4**: Client bucket state must be stored in a bounded LRU structure (`KeyedLimiter`, default 10,000 clients) to prevent client-ID memory exhaustion attacks.
  - **FR-1.5**: Support optional traffic throttling via a bounded FIFO leaky bucket that delays requests up to a configurable maximum wait threshold before rejecting with 429.

- **FR-2: Asynchronous Job Model & Bounded Backpressure**
  - **FR-2.1**: `POST /screen` must validate inputs, enqueue a batch processing task, and immediately respond with HTTP 202 Accepted containing a unique `job_id`.
  - **FR-2.2**: The job queue must be strictly bounded (`queue_max_size`, default 50). If the queue is saturated, `POST /screen` must reject requests with HTTP 503 Service Unavailable and a `Retry-After` header.
  - **FR-2.3**: `GET /jobs/{id}` must return job status (`queued`, `running`, `done`, `failed`), timestamps, and item progress counts.
  - **FR-2.4**: `GET /results/{id}` must return the final ranking JSON (identical schema to baseline `results.json`) once the job is in `done` state, or HTTP 404/400 if not available/incomplete.
  - **FR-2.5**: The system must maintain CLI backwards compatibility (`main.py --input <dir> --output <path>`).

- **FR-3: Thread-Safe, Memory-Bounded, TTL-Aware LRU Cache**
  - **FR-3.1**: Cache core must implement a hand-written doubly linked list (`prev`/`next` node pointers with sentinel head and tail) and hash map (`dict[key, Node]`) ensuring strictly $O(1)$ amortized `get`, `put`, and `evict`.
  - **FR-3.2**: A secondary `OrderedDict`-based LRU cache variant must be provided for reference, property tests, and benchmarking.
  - **FR-3.3**: Support Segmented LRU (SLRU) with probationary and protected partitions to provide scan resistance against bulk resume loads.
  - **FR-3.4**: Support per-entry TTL expiry via lazy expiration on `get()` and an active min-heap `(expires_at, key, version)` swept by a background daemon thread. Stale heap entries must be skipped via entry versioning.
  - **FR-3.5**: Memory bounds must track `size_bytes` per entry. When `total_bytes > max_bytes` or `len > max_items`, the cache must evict entries from the LRU tail until bounds are satisfied.

- **FR-4: Lock Striping & Stampede Suppression (Single-Flight)**
  - **FR-4.1**: `ShardedLRUCache` must partition keys across $N$ independent shards (`hash(key) % N`, default $N=16$), each protected by an independent `threading.Lock`.
  - **FR-4.2**: Threads accessing distinct shards must execute in parallel without lock contention.
  - **FR-4.3**: A strict lock-ordering contract must guarantee that no thread ever holds more than one shard lock simultaneously, eliminating deadlocks.
  - **FR-4.4**: Lock striping must strictly separate lock acquisition from computation: no shard lock may ever be held while executing an external loader or network I/O.
  - **FR-4.5**: A `SingleFlight` coordinator must suppress cache stampedes (thundering herds): when multiple threads miss on the same key, only the first caller invokes the loader; concurrent callers wait on a shared `Future`/`Event` and receive the same loaded result.

- **FR-5: Outbound Adaptive Rate Limiting for Upstream APIs**
  - **FR-5.1**: Outbound calls to GitHub and LLM APIs must be regulated by an `OutboundLimiter` combining token bucket rate limiting with `asyncio.Semaphore` concurrency limiting.
  - **FR-5.2**: The limiter must inspect response headers (`x-ratelimit-remaining`, `x-ratelimit-reset`, and `Retry-After`). If remaining quota approaches zero, the token bucket must pause until the reset timestamp.
  - **FR-5.3**: Upon receiving an HTTP 429 or 503 response, the limiter must apply exponential backoff with full jitter up to a capped maximum retry threshold.

- **FR-6: Runtime Memory Guard & Eviction Hysteresis**
  - **FR-6.1**: A background `MemoryGuard` daemon thread must periodically inspect aggregate cache memory and process Resident Set Size (RSS) using `psutil` (falling back to `resource`).
  - **FR-6.2**: Memory eviction must employ two-level watermarks to prevent thrashing:
    - High Watermark: 80% of `cache_max_bytes`.
    - Low Watermark: 60% of `cache_max_bytes`.
  - **FR-6.3**: When cache usage crosses the high watermark, the `MemoryGuard` must trigger tail evictions across all registered caches until aggregate memory falls below the low watermark.

- **FR-7: Observability & Health Endpoints**
  - **FR-7.1**: `GET /health` must return liveness and readiness status (`status: "ok"`, worker pool status, memory health).
  - **FR-7.2**: `GET /metrics` must expose JSON operational metrics including:
    - Cache metrics: `entries`, `bytes`, `hits`, `misses`, `hit_ratio`, `evictions`, `expirations`.
    - System metrics: `rss_mb`, `uptime_seconds`.
    - Limiter metrics: `inbound_rate_limited_total`, `outbound_throttled_total`.
    - Worker metrics: `jobs_queued`, `jobs_running`, `jobs_completed`, `jobs_failed`.

- **FR-8: Graceful Shutdown & Persistence**
  - **FR-8.1**: On receiving `SIGTERM` or `SIGINT`, the service must immediately stop accepting new jobs, reject new submissions with 503, drain the active job queue within a configurable timeout (`shutdown_timeout_s`, default 15s), and join worker threads.
  - **FR-8.2**: Cache state snapshots must be written atomically to disk (write to a temporary file, flush/sync, then `os.replace`) to prevent file corruption during shutdown.

---

### 3. Non-Functional Requirements (NFR)

- **NFR-1: Throughput and Latency Performance**
  - **NFR-1.1**: The p95 response latency for cached lookups and API metadata endpoints (`GET /jobs/{id}`, `GET /results/{id}`, `GET /metrics`, `GET /health`) must be $\le 15$ ms under standard load.
  - **NFR-1.2**: 50-resume batch processing throughput must achieve at least a $2.5\times$ speedup over the baseline sequential parsing (~15 s baseline reduced to $\le 6$ s on 4 CPU cores) via `ProcessPoolExecutor`.

- **NFR-2: Memory Boundedness & Stability**
  - **NFR-2.1**: Under an insertion load of 1,000,000 keys, cache heap usage must remain strictly bounded below `cache_max_bytes` (default 256 MB) + static runtime overhead.
  - **NFR-2.2**: The process must exhibit flat memory allocation profiles in `tracemalloc` following the initial warmup phase.

- **NFR-3: Cache Hit Efficiency**
  - **NFR-3.1**: Repeat runs over unchanged resume batches must achieve a cache hit ratio $\ge 80\%$, eliminating redundant PDF parsing, GitHub API requests, and LLM calls.

- **NFR-4: Concurrency Invariants & Deadlock Freedom**
  - **NFR-4.1**: Under a concurrent stress workload of 32 threads performing 100,000 operations simultaneously, the system must produce zero unhandled exceptions, zero data races, and exact internal invariant consistency:
    - $\text{len}(\text{hash\_map}) \equiv \text{len}(\text{doubly\_linked\_list})$.
    - $\sum \text{node.size\_bytes} \equiv \text{cache.total\_bytes}$.
  - **NFR-4.2**: The lock striping architecture must be formally deadlock-free by design (verified via test suite timeout assertions).

- **NFR-5: Determinism & Golden Byte-Identical Output**
  - **NFR-5.1**: For identical inputs (`resumes/` folder, LLM disabled, GitHub disabled), the ranking output (`ranked_candidates`, `rejected_candidates`, scores, breakdowns, and evidence) must be **byte-identical** to the baseline golden `results.json`.
  - **NFR-5.2**: All time-based calculations (rate limit refills, TTL checks, metrics) must use `time.monotonic()` through an injectable `Clock` interface, ensuring 100% deterministic testing.

- **NFR-6: Portability & Standard Library Primacy**
  - **NFR-6.1**: Must run on Python 3.12+ with zero external storage services (no Redis, no database).
  - **NFR-6.2**: All concurrent synchronization primitives must rely on Python standard library modules (`threading`, `queue`, `heapq`, `concurrent.futures`, `asyncio`).

---

### 4. Risk Register & Mitigations

| Risk ID | Description & Potential Impact | Likelihood | Severity | Mitigation Strategy |
|---|---|---|---|---|
| **RSK-01** | **Cache Stampede (Thundering Herd)**<br>When hot keys expire or during a cold start, concurrent threads miss simultaneously and fire parallel requests at GitHub/LLM, causing 429/403 rate bans. | High | Critical | Implement `SingleFlight` coordinator per key. Only the first thread initiates the upstream call; concurrent threads park on an event/future and receive the identical result. |
| **RSK-02** | **Lock Contention & Deadlock**<br>Coarse-grained cache locking degrades multi-threaded throughput. Acquiring multiple locks in different orders leads to circular deadlocks. | Medium | Critical | Implement lock striping (`ShardedLRUCache`, 16 shards). Enforce strict lock-ordering contract: a thread never acquires more than one shard lock simultaneously. Shard locks are strictly released before invoking loaders. |
| **RSK-03** | **Memory Exhaustion via Client Keys (DoS)**<br>An attacker floods the API with randomized client IDs or IP addresses, causing unbounded growth in rate-limiter tables. | High | High | Store per-client rate limiter state in a bounded `LRUCache` (`KeyedLimiter`, capacity 10,000). Least-recently-used inactive client buckets are evicted automatically. |
| **RSK-04** | **Upstream Quota Ban (GitHub/LLM)**<br>Bursts of requests exceed GitHub's 60 req/h unauthenticated quota or LLM rate limits, causing entire batch failures. | High | High | Implement adaptive `OutboundLimiter`. Read `x-ratelimit-remaining` and pause token dispatch until `reset` timestamp when remaining nears zero. Apply exponential backoff with full jitter on 429/503. |
| **RSK-05** | **Clock Skew & Wall-Clock Drift**<br>NTP adjustments, leap seconds, or daylight savings cause negative elapsed time or premature TTL expirations. | Medium | Medium | Exclusively use `time.monotonic()` for all TTL calculations, token bucket refill math, and timeouts. Inject clock dependency (`Callable[[], float]`) throughout. |
| **RSK-06** | **Cache Eviction Thrashing**<br>Evicting cache items to the exact upper ceiling causes immediate re-eviction upon the next insertion. | Medium | Medium | Implement two-level watermark eviction with hysteresis in `MemoryGuard` (high watermark 80%, low watermark 60%), mimicking OS page replacement algorithms (`kswapd`). |
| **RSK-07** | **Inconsistent State on Sudden Termination**<br>Process termination during cache persistence leaves corrupt JSON cache files on disk. | Medium | Medium | Perform atomic snapshot persistence: serialize cache to a temporary file (`.tmp`), execute `fsync()`, and atomically overwrite target path with `os.replace()`. |

---

### 5. Requirements Traceability Matrix

| Requirement | Description | Target Module(s) | Verification / Test Suite |
|---|---|---|---|
| **FR-1** | Inbound Rate Limiting & Throttling | `src/screener/core/rate_limiter.py`<br>`src/screener/api/middleware.py` | `tests/unit/test_rate_limiter.py`<br>`tests/integration/test_api_rate_limit.py` |
| **FR-2** | Async Job Model & Backpressure | `src/screener/services/job_service.py`<br>`src/screener/api/app.py` | `tests/integration/test_job_service.py` |
| **FR-3** | Bounded LRU Cache with TTL | `src/screener/core/lru_cache.py`<br>`src/screener/core/clock.py` | `tests/unit/test_lru_cache.py`<br>`tests/property/test_lru_property.py` |
| **FR-4** | Lock Striping & Single-Flight | `src/screener/core/sharded_cache.py`<br>`src/screener/core/single_flight.py` | `tests/concurrency/test_cache_concurrency.py`<br>`tests/concurrency/test_stampede.py` |
| **FR-5** | Adaptive Outbound Rate Limiting | `src/screener/core/rate_limiter.py`<br>`src/screener/adapters/outbound_client.py` | `tests/unit/test_outbound_limiter.py` |
| **FR-6** | MemoryGuard with Hysteresis | `src/screener/core/memory_guard.py` | `tests/unit/test_memory_guard.py` |
| **FR-7** | Observability & Metrics (`/metrics`) | `src/screener/core/metrics.py`<br>`src/screener/api/app.py` | `tests/integration/test_api_rate_limit.py` |
| **FR-8** | Graceful Shutdown & Persistence | `src/screener/api/app.py`<br>`src/screener/services/workers.py` | `tests/integration/test_job_service.py` |
| **NFR-1** | Latency & ProcessPool Throughput | `src/screener/services/workers.py` | `tests/benchmarks/test_throughput.py` |
| **NFR-2** | Memory Boundedness (1M keys) | `src/screener/core/lru_cache.py`<br>`src/screener/core/memory_guard.py` | `tests/unit/test_memory_guard.py` |
| **NFR-3** | Cache Hit Efficiency ($\ge 80\%$) | `src/screener/adapters/cache_adapter.py` | `tests/integration/test_job_service.py` |
| **NFR-4** | Concurrency Invariants & Deadlock Free | `src/screener/core/sharded_cache.py` | `tests/concurrency/test_cache_concurrency.py` |
| **NFR-5** | Golden Byte-Identical Ranking | Domain adapters & pipeline | `tests/golden/test_golden_ranking.py` |
| **NFR-6** | Standard Library Primacy | All modules | Static code inspection & imports check |
