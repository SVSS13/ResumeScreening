# ADR-004: Runtime Memory Management & Watermark Eviction

## Status
Accepted

## Context
Under continuous production operations or large batch workloads, in-memory caches and data representations can consume unbounded RAM, triggering system out-of-memory (OOM) killer terminations.
Simple item-count limits fail to control memory because individual cache entries vary wildly in byte footprint (e.g., small metadata strings vs large parsed resume blocks).
Moreover, evicting entries only when a strict ceiling is crossed causes "thrashing"—where each subsequent insertion immediately incurs eviction overhead.

## Decision

### 1. Byte-Aware Accounting
Every cache entry calculates and tracks its approximate memory footprint:
- For strings and bytes: exact length (`len(data)`).
- For dictionaries/dataclasses: recursive memory estimation via `sys.getsizeof` / structural inspection.
- The cache maintains `total_bytes` as a running counter updated in $O(1)$ on insertions, updates, and evictions.

### 2. OS Memory Model Analogy: Watermark Hysteresis
We adopt the memory management strategy used by OS kernels (e.g., Linux virtual memory page reclamation daemon `kswapd`):
- **Budget**: Total cache memory ceiling $M_{\text{max}}$ (default 256 MB), distributed across caches.
- **High Watermark ($W_{\text{high}}$)**: 80% of $M_{\text{max}}$.
- **Low Watermark ($W_{\text{low}}$)**: 60% of $M_{\text{max}}$.
- **Hysteresis Principle**:
  When cache consumption exceeds $W_{\text{high}}$, the system does not simply evict one item; it aggressively evicts tail items until usage drops below $W_{\text{low}}$.
  This creates a 20% headroom buffer, ensuring the system does not repeatedly thrash on subsequent insertions.

### 3. Background `MemoryGuard` Daemon
A dedicated background thread (`MemoryGuard`) executes periodically (default every 5 seconds):
- Inspects aggregate cache bytes across all registered caches (`parse_cache`, `github_cache`, `llm_cache`).
- Inspects process Resident Set Size (RSS) using `psutil.Process().memory_info().rss` (falling back to `resource.getrusage(RUSAGE_SELF).ru_maxrss`).
- If total cache memory exceeds $W_{\text{high}}$ OR process RSS exceeds a configured soft ceiling (`rss_soft_limit_mb`), it triggers coordinated LRU tail eviction down to $W_{\text{low}}$.

### 4. Compact Representation & Deduplication
- The pipeline stores parsed summaries and token vectors, never retaining raw PDF byte arrays in memory.
- Candidate text is never duplicated between parse caches and feature caches.

## Alternatives Considered & Rejected
- **Per-Insert Synchronous Eviction to Hard Ceiling**: Rejected because running eviction logic on every single insert under heavy load causes lock contention and latency spikes.
- **Garbage Collection (GC) Forcing (`gc.collect()`)**: Periodic manual GC calls trigger stop-the-world pauses and do not release unreferenced memory back to the OS.

## Consequences
- **Positive**: Strict memory bounding; flat RSS trajectory after warm-up; zero cache thrashing under sustained load.
- **Negative**: Background monitoring thread runs continuously, but sleeping 5s between checks incurs negligible CPU overhead (<0.01%).
