# Architecture Defense & Technical Walkthrough Guide

This document equips the candidate to defend the 7 key architectural decisions made in the AI Resume Screening & Ranking Platform during technical interview rounds.

---

### Decision 1: Segmented LRU (SLRU) with Min-Heap TTL vs. Raw `OrderedDict`
- **3-Sentence Rationale:** 
  A standard LRU cache suffers from recency pollution when sequential batch scans evict frequently accessed items. We split the cache into a probationary partition (20%) and a protected partition (80%), requiring a key to be accessed at least twice before promotion. Lazy TTL expiration is managed via a min-heap augmented by monotonic version numbers, enabling $O(1)$ amortized eviction without background thread locking overhead.
- **Concrete Trade-off:** 
  Higher metadata overhead per cache entry (DLL node + min-heap tuple + version integer, ~128 bytes per item) compared to `functools.lru_cache`, but provides immune defense against scan thrashing.
- **Likely Interviewer Question:** 
  *“Why not just use Python’s built-in `functools.lru_cache` or a third-party Redis cache?”*
  - **Concise Answer:** 
    *“`functools.lru_cache` does not support TTL expiration, per-entry byte memory bounds, or SLRU segmented partitions, and Redis would introduce an external network hop, serialization overhead, and an operational dependency that violates the zero-external-dependency requirement of this service.”*

---

### Decision 2: Lock Striping (`ShardedLRUCache`) vs. Coarse-Grained Synchronization
- **3-Sentence Rationale:** 
  A single global lock serializes all cache reads and writes across worker threads, creating a bottleneck under high concurrency. We partition cache keys across $N=16$ independent shards using power-of-two bitwise modulo hashing (`hash(key) & 15`), each protected by its own mutex. This allows threads accessing different keys to read, write, and evict completely in parallel.
- **Concrete Trade-off:** 
  Cross-shard aggregate operations like `len()`, `stats()`, and global watermark sweeps require acquiring shard locks sequentially rather than atomically across all shards at once.
- **Likely Interviewer Question:** 
  *“How do you guarantee that cross-shard operations never cause a deadlock?”*
  - **Concise Answer:** 
    *“We enforce a strict ascending lock-ordering invariant: shards are always acquired sequentially from $S_0$ through $S_{15}$, releasing each lock before acquiring the next, and no thread is ever permitted to hold two shard locks simultaneously.”*

---

### Decision 3: Single-Flight Coordination (Cache Stampede Suppression)
- **3-Sentence Rationale:** 
  When hundreds of concurrent requests experience a simultaneous cache miss on the same key (e.g., an expensive GitHub API lookup or LLM call), all workers would redundantly invoke the upstream service. `SingleFlight` coordinates concurrent callers so that only the first thread executes the loader, while subsequent callers register and wait on a shared event. When the first thread completes, it notifies all waiting threads with the identical result.
- **Concrete Trade-off:** 
  Adds $O(1)$ coordination dictionary synchronization overhead and requires careful exception propagation so that an upstream failure unblocks waiting callers cleanly.
- **Likely Interviewer Question:** 
  *“Is a lock held while the external upstream loader executes?”*
  - **Concise Answer:** 
    *“Never. The registration lock is acquired only to register the in-flight event and released immediately; the slow loader executes completely outside all locks, which we formally proved with a concurrent timeout test.”*

---

### Decision 4: Two-Tier GIL-Aware Concurrency (`ProcessPool` + `asyncio` + `ThreadPool`)
- **3-Sentence Rationale:** 
  Python’s Global Interpreter Lock (GIL) prevents CPU-bound tasks like PDF text extraction and scikit-learn TF-IDF matrix calculations from scaling across multiple threads. We route CPU-bound text extraction to a `ProcessPoolExecutor` with pre-forked workers, I/O-bound GitHub network calls to native `asyncio`, and atomic disk persistence to a bounded `ThreadPoolExecutor`. This separation isolates I/O latency from compute throughput.
- **Concrete Trade-off:** 
  Inter-process communication requires serialization (pickling) of resume strings across process boundaries, adding slight memory copy overhead.
- **Likely Interviewer Question:** 
  *“Why not run the entire pipeline in asyncio with async file reads?”*
  - **Concise Answer:** 
    *“PDF parsing in `pdfminer`/`pypdf` is compute-intensive and blocks the Python event loop for hundreds of milliseconds; offloading parsing to dedicated process workers keeps the FastAPI event loop responsive with sub-millisecond p95 latency.”*

---

### Decision 5: Proactive Watermark Eviction with Hysteresis (`MemoryGuard`)
- **3-Sentence Rationale:** 
  Waiting for an OS Out-Of-Memory (OOM) killer to terminate the container is unacceptable in production services. Inspired by OS page reclamation (`kswapd`), a background daemon thread monitors process Resident Set Size (RSS) and aggregate cache bytes every 5 seconds. When memory breaches the high watermark (80%), it proactively evicts entries across shards down to the low watermark (60%), creating a 20% headroom buffer.
- **Concrete Trade-off:** 
  Background thread introduces minimal background CPU consumption (~0.1%) and requires cross-platform RSS extraction (handling Linux `/proc`, macOS Darwin byte units, and Windows fallbacks).
- **Likely Interviewer Question:** 
  *“Why use hysteresis instead of just evicting 1 item whenever the high watermark is reached?”*
  - **Concise Answer:** 
    *“Evicting only 1 item causes immediate eviction thrashing on every subsequent insert; evicting down to the low watermark restores a 20% headroom buffer, allowing hundreds of new entries to be cached without triggering continuous reclamation.”*

---

### Decision 6: Token Bucket Inbound Rate Limiting with RFC 6585 Headers
- **3-Sentence Rationale:** 
  Unbounded ingress exposes the screening pipeline to denial-of-service and memory exhaustion. We implemented a continuous replenishment token bucket per client identity stored in an LRU bucket table. Requests exceeding burst capacity return HTTP `429 Too Many Requests` with RFC 6585 headers (`Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`).
- **Concrete Trade-off:** 
  Token bucket state consumes memory in the LRU table (~128 bytes per active client IP), requiring bounded capacity to prevent memory DoS attacks.
- **Likely Interviewer Question:** 
  *“What is the difference between a Token Bucket and a Leaky Bucket?”*
  - **Concise Answer:** 
    *“A Token Bucket allows bursty traffic up to bucket capacity while enforcing an average rate over time, making it ideal for web API burstiness; a Leaky Bucket forces requests through at a strictly constant rate, smoothing traffic but introducing queue delay.”*

---

### Decision 7: "LLM as Witness, Code as Judge" (Fail-Closed Architecture)
- **3-Sentence Rationale:** 
  Prompting an LLM to directly assign final candidate ranks is non-deterministic, unexplainable, and prone to prompt injection. Hard eligibility filters (Python stack, AI project presence) and 100-point scoring weights are executed as deterministic Python code. If an LLM is enabled, its role is restricted to that of an advisory witness that extracts structured Pydantic project evidence and verbatim quotes.
- **Concrete Trade-off:** 
  Heuristics require explicit keyword dictionaries and semantic patterns, but produce 100% reproducible, auditable, and defendable rankings.
- **Likely Interviewer Question:** 
  *“How do you prevent candidates from gaming the system by keyword-stuffing their resume?”*
  - **Concise Answer:** 
    *“Our scoring model requires contextual evidence in projects or experience sections rather than skill lists alone, pairs TF-IDF semantic relevance with exact project-depth heuristics, and applies -5 to -15 point deductions for shallow wrapper projects.”*
