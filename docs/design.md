# Phase 2: System Architecture & Technical Design Document

## 1. System Overview

The restructured Resume Screening Backend delivers an asynchronous, concurrent, rate-limited, and memory-bounded pipeline. It enforces strict separation of concerns across layers while preserving the deterministic domain scoring logic without any modifications.

```
                           +------------------------+
                           |  External API Clients  |
                           +-----------+------------+
                                       |
                                HTTP Requests
                                       |
               +-----------------------v-----------------------+
               |                 FastAPI Layer                 |
               |                                               |
               |  +-----------------------------------------+  |
               |  |          RateLimitMiddleware            |  |
               |  |  (Token Bucket per client_id in LRU)    |  |
               |  +--------------------+--------------------+  |
               +-----------------------|-----------------------+
                                       | 202 Accepted (job_id)
               +-----------------------v-----------------------+
               |                  JobService                   |
               |  - Bounded asyncio.Queue (maxsize=50)         |
               |  - Backpressure: 503 + Retry-After if full     |
               |  - Bounded In-Memory Job Store (LRU 1k jobs)  |
               +-----------------------+-----------------------+
                                       |
                                 Worker Runtime
                                       |
       +-------------------------------+-------------------------------+
       |                               |                               |
       v                               v                               v
+--------------------+      +--------------------+      +--------------------+
| ProcessPool (CPU)  |      |   asyncio (I/O)    |      |  ThreadPool (Disk) |
| - PDF/DOCX Parsing |      | - GitHub API       |      | - Disk persistence |
| - Text Extraction  |      | - LLM API Calls    |      | - Atomic snapshots |
| - TF-IDF Matrix    |      |   (httpx async)    |      |   (os.replace)     |
+---------+----------+      +---------+----------+      +---------+----------+
          |                           |                           |
          +-------------+             |             +-------------+
                        |             |             |
+-----------------------v-------------v-------------v------------------------+
|                          ShardedLRUCache Layer                             |
|  - 16 Independent Shards: hash(key) % 16, Lock Striping                    |
|  - Single-Flight Coordinator (Stampede / Thundering-Herd Suppression)       |
|  - Hand-Crafted Doubly Linked List + Hash Map (O(1) get/put/evict)         |
|  - TTL Min-Heap with Version Check & Lazy Expiration                       |
|  - Segmented LRU (SLRU): Probation (20%) + Protected (80%)                |
|                                                                            |
|   [ParseCache]               [GitHubCache]                 [LLMCache]      |
|   key=sha256(content)        key=username, TTL=24h         key=sha256(...) |
+-------------------------------------+--------------------------------------+
                                      |
                         +------------v------------+
                         |       MemoryGuard       |
                         |  (Daemon Thread: 5s)    |
                         |  - RSS + Cache Bytes    |
                         |  - High Watermark (80%) |
                         |  - Low Watermark (60%)  |
                         |  - Hysteresis Eviction  |
                         +-------------------------+
```

---

## 2. Sequence Diagrams (Mermaid)

### 2.1 Single-Flight Cache Lookup and Loader Flow
Prevents cache stampedes when multiple concurrent requests encounter a cache miss on the same key.

```mermaid
sequenceDiagram
    autonumber
    actor Thread1 as Client Thread A
    actor Thread2 as Client Thread B
    participant SF as SingleFlight
    participant Shard as ShardedLRUCache
    participant Upstream as External Loader (GitHub / LLM)

    Thread1->>Shard: get(key)
    Note over Shard: Acquire Shard Lock
    Shard-->>Thread1: Cache Miss
    Note over Shard: Release Shard Lock

    Thread1->>SF: execute(key, loader)
    Note over SF: Acquire SingleFlight Lock
    Note over SF: Register in_flight[key] = Future/Event
    Note over SF: Release SingleFlight Lock

    Thread2->>Shard: get(key)
    Note over Shard: Acquire Shard Lock
    Shard-->>Thread2: Cache Miss
    Note over Shard: Release Shard Lock

    Thread2->>SF: execute(key, loader)
    Note over SF: Acquire SingleFlight Lock
    Note over SF: Key found in in_flight!
    Note over SF: Release SingleFlight Lock
    Note over Thread2: Thread B awaits existing Future

    Note over Thread1: Thread A executes loader (Lock NOT held!)
    Thread1->>Upstream: Call External Service / Parse
    Upstream-->>Thread1: Loaded Value V

    Thread1->>Shard: put(key, V)
    Note over Shard: Acquire Shard Lock
    Note over Shard: Insert into MRU head
    Note over Shard: Release Shard Lock

    Thread1->>SF: complete(key, V)
    Note over SF: Set Future result = V
    Note over SF: Remove key from in_flight
    SF-->>Thread2: Wake up Thread B with V
    Thread1-->>Thread1: Return V
    Thread2-->>Thread2: Return V
```

---

### 2.2 Token Bucket Inbound Rate Limiting Middleware
Enforces per-client quotas and injects RFC-standard rate limit response headers.

```mermaid
sequenceDiagram
    autonumber
    actor Client as API Client
    participant MW as RateLimitMiddleware
    participant KL as KeyedLimiter (LRU)
    participant TB as TokenBucket (Client ID)
    participant App as FastAPI Route Handler

    Client->>MW: HTTP Request (POST /screen)
    MW->>MW: Extract Identity (X-API-Key or IP)

    MW->>KL: get_or_create(client_id, route_type)
    Note over KL: Lookup in bounded LRU cache
    KL-->>MW: TokenBucket instance

    MW->>TB: try_acquire(cost=1.0)
    Note over TB: now = clock()
    Note over TB: delta = now - last_refill
    Note over TB: tokens = min(cap, tokens + delta * rate)
    Note over TB: last_refill = now

    alt tokens >= cost
        Note over TB: tokens = tokens - cost
        TB-->>MW: Allowed (remaining_tokens, reset_time)
        MW->>App: Forward Request
        App-->>MW: HTTP Response (202 Accepted)
        Note over MW: Inject X-RateLimit-* headers
        MW-->>Client: HTTP 202 Accepted + RateLimit Headers
    else tokens < cost
        Note over TB: retry_after = (cost - tokens) / rate
        TB-->>MW: Denied (retry_after, remaining_tokens, reset_time)
        Note over MW: Build 429 Payload
        MW-->>Client: HTTP 429 Too Many Requests<br/>(Retry-After, X-RateLimit-*)
    end
```

---

### 2.3 Runtime Memory Eviction with Hysteresis (MemoryGuard)
Monitors system RSS and aggregate cache memory, executing batched evictions down to the low watermark to avoid cache thrashing.

```mermaid
sequenceDiagram
    autonumber
    participant MG as MemoryGuard (Daemon)
    participant OS as OS / Process (psutil / resource)
    participant Caches as [ParseCache, GitHubCache, LLMCache]
    participant Metrics as MetricsCollector

    loop Every period_s (default 5 seconds)
        MG->>OS: Read Process RSS (MB)
        MG->>Caches: Query sum(total_bytes)
        Caches-->>MG: Total Cached Bytes B

        alt B > High Watermark (0.80 * max_bytes) OR RSS > soft_limit
            Note over MG: Memory pressure detected!
            MG->>Metrics: Increment watermark_breaches_total
            Note over MG: target_bytes = Low Watermark (0.60 * max_bytes)
            Note over MG: evict_needed = B - target_bytes

            loop Over Each Registered Cache
                MG->>Caches: evict_to(proportional_target)
                Note over Caches: Acquire Shard Lock sequentially
                Note over Caches: Evict from tail.prev until target reached
                Note over Caches: Release Shard Lock
            end

            MG->>Metrics: Record evictions & new byte count
            Note over MG: Headroom buffer (20%) established!
        else Memory within safe limits
            Note over MG: Safe, no action required
        end
        Note over MG: Sleep period_s
    end
```

---

### 2.4 Graceful Shutdown Sequence
Drains the in-flight job queue and executes atomic cache persistence to prevent corrupt snapshots.

```mermaid
sequenceDiagram
    autonumber
    actor Admin as OS / Docker (SIGTERM / SIGINT)
    participant App as FastAPI Lifespan / App
    participant JS as JobService
    participant Workers as Worker Runtime
    participant Caches as ShardedLRUCache
    participant Disk as Filesystem

    Admin->>App: Send SIGTERM
    Note over App: Trigger shutdown lifecycle hook
    App->>JS: stop_accepting_jobs()
    Note over JS: Ingress closed; new POST /screen returns 503

    App->>JS: drain_queue(timeout=shutdown_timeout_s)
    Note over JS: Wait for actively running jobs to complete
    JS->>Workers: join_active_tasks()
    Workers-->>JS: All active tasks finished

    App->>Caches: snapshot_to_disk()
    Note over Caches: Serialize state to temporary file (cache.json.tmp)
    Caches->>Disk: write_text(json_data) + fsync()
    Note over Caches: Atomic replacement
    Caches->>Disk: os.replace(cache.json.tmp, cache.json)
    Disk-->>Caches: Replacement complete

    App->>Workers: shutdown(wait=True)
    Note over Workers: Shutdown ProcessPool and ThreadPool
    App-->>Admin: Process terminates cleanly (Exit 0)
```

---

## 3. Component Complexity & Thread-Safety Guarantees

| Module / Class | Public Method | Time Complexity | Space Complexity | Thread-Safety Guarantee | Locking Mechanism |
|---|---|---|---|---|---|
| `LRUCache` | `get(key)` | $O(1)$ amortized | $O(1)$ | Thread-safe when accessed via shard | Hand-crafted DLL pointer update; lazy TTL check |
| `LRUCache` | `put(key, val, ttl)` | $O(1)$ amortized | $O(1)$ | Thread-safe when accessed via shard | Unlinks LRU tail when full; version increment |
| `LRUCache` | `evict_to(target)` | $O(K)$ where $K$ = evicted nodes | $O(1)$ | Thread-safe when accessed via shard | Repeated tail unlinking until within target |
| `ShardedLRUCache` | `get(key)` | $O(1)$ | $O(1)$ | **Fully Thread-Safe** | Per-shard `threading.Lock` (`hash(key) % N`) |
| `ShardedLRUCache` | `put(key, val, ttl)` | $O(1)$ amortized | $O(1)$ | **Fully Thread-Safe** | Per-shard `threading.Lock` |
| `ShardedLRUCache` | `get_or_load(key, loader)` | $O(1)$ cache hit<br>$O(\text{loader})$ on miss | $O(1)$ | **Fully Thread-Safe** | Lock released before calling loader; SingleFlight coordination |
| `SingleFlight` | `execute(key, loader)` | $O(1)$ sync overhead | $O(1)$ | **Fully Thread-Safe** | Internal `threading.Lock` protecting `in_flight` dict; callers wait on `Future` |
| `TokenBucket` | `try_acquire(cost)` | $O(1)$ | $O(1)$ | **Thread-Safe** | `threading.Lock` protecting token calculation |
| `KeyedLimiter` | `check(key, cost)` | $O(1)$ amortized | $O(\text{max\_keys})$ | **Thread-Safe** | Internal `LRUCache` synchronization |
| `OutboundLimiter` | `acquire()` | $O(1)$ amortized | $O(1)$ | **Async Thread-Safe** | `asyncio.Semaphore` + TokenBucket + pause event |
| `OutboundLimiter` | `observe(headers, status)` | $O(1)$ | $O(1)$ | **Async Thread-Safe** | Parses headers, sets pause event on reset |
| `MemoryGuard` | `run_cycle()` | $O(N)$ where $N$ = shards | $O(1)$ | **Thread-Safe** | Iterates over shards sequentially; acquires 1 lock at a time |
| `JobService` | `submit(job)` | $O(1)$ | $O(1)$ | **Async Thread-Safe** | Thread-safe bounded `asyncio.Queue` |
| `JobService` | `get_job(id)` | $O(1)$ | $O(1)$ | **Async Thread-Safe** | Synchronized in-memory job store |

---

## 4. Lock Ordering Contract

To formally guarantee deadlock freedom across the application, the following lock-acquisition invariants are strictly enforced:

1. **Hierarchy Level 1: Global Managers**: `SingleFlight` internal mutex. Never held while acquiring shard locks.
2. **Hierarchy Level 2: Shard Mutexes**: Shard locks $S_0, S_1, \dots, S_{N-1}$.
   - **Rule**: A thread may hold **at most ONE** shard lock $S_i$ at any point in time.
   - **Rule**: If a thread holds $S_i$, it is structurally forbidden from attempting to acquire $S_j$ ($j \ne i$).
   - **Rule**: Shard locks must be released before calling any external loader, disk write, or network call.
3. **Hierarchy Level 3: Token Bucket Mutexes**: Individual token bucket locks. Independent of cache shard locks.
4. **Cross-Shard Sequential Traversal**: When `MemoryGuard` or `stats()` computes totals across all shards, it acquires and releases shard locks sequentially ($S_0 \to \text{release} \to S_1 \to \text{release} \dots$), never holding multiple locks simultaneously.
