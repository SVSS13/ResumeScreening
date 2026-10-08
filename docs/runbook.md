# Operations Runbook: Resume Screener Backend

## 1. System Overview
The backend service processes resume batches through an asynchronous job pipeline with bounded queues, multi-core process pools, adaptive outbound throttling, and an in-process lock-striped LRU cache protected by a memory guard daemon.

---

## 2. Configuration & Tuning Knobs

All knobs can be set via environment variables or loaded through JSON configuration files:

| Environment Variable | Default | Purpose | When to Adjust |
|---|---|---|---|
| `CACHE_MAX_BYTES` | `268435456` (256 MB) | Aggregate memory cap across caches | Increase if server has ample RAM; decrease in container constrained environments |
| `CACHE_SHARD_COUNT` | `16` | Number of striped locks | Increase to 32 or 64 if thread profiling reveals lock contention on cache |
| `MEMORY_HIGH_WATERMARK`| `0.80` | Threshold triggering batch eviction | Lower if process RSS approaches container memory limits |
| `MEMORY_LOW_WATERMARK` | `0.60` | Target ceiling after eviction | Increase if cache hit ratio drops excessively after eviction cycles |
| `MEMORY_RSS_SOFT_LIMIT_MB`| `512.0` | Process RSS soft ceiling | Set to ~70% of Docker container memory limit |
| `RATE_LIMIT_WRITE_BURST`| `2` | Max burst for `POST /screen` | Increase for trusted high-volume client API keys |
| `RATE_LIMIT_WRITE_REFILL_PER_S`| `0.1` | Refill rate (6 req/min) | Adjust according to CPU worker capacity |
| `WORKER_PROCESSES` | `min(CPU, 8)` | CPU processes for parsing | Scale with physical CPU cores |
| `JOB_QUEUE_MAX_SIZE` | `50` | Maximum queued batch jobs | Decrease to fail fast under sustained overload (backpressure) |

---

## 3. How to Read `/metrics`

Example JSON output from `GET /metrics`:
```json
{
  "entries": 1520,
  "bytes": 14285700,
  "hits": 4510,
  "misses": 340,
  "hit_ratio": 0.9299,
  "evictions": 42,
  "expirations": 18,
  "rss_mb": 118.4,
  "rate_limited_total": 5,
  "outbound_throttled_total": 0,
  "uptime_seconds": 3600.0,
  "caches": {
    "parse_cache": {
      "entries": 50,
      "bytes": 245000,
      "hits": 120,
      "misses": 50,
      "hit_ratio": 0.7059
    }
  }
}
```

### Key Health Indicators:
- **`hit_ratio` $\ge 0.80$**: Cache is effectively absorbing repetitive workloads. If $< 0.50$, examine if batch scans are flushing keys or if TTL is too short.
- **`rate_limited_total`**: Number of inbound requests rejected with HTTP 429. Spikes indicate an aggressive or misbehaving client.
- **`rss_mb`**: Resident Set Size. Should stabilize and remain flat post-warmup.
- **`outbound_throttled_total`**: Number of requests paused due to upstream `x-ratelimit-*` headers. If climbing rapidly, consider configuring `GITHUB_TOKEN` to raise upstream limits.

---

## 4. Incident Response & Troubleshooting

### Symptom 1: Inbound HTTP 429 Storms
- **Cause**: Client or load testing tool sending requests faster than the configured token bucket refill rate.
- **Remedy**:
  1. Inspect `X-RateLimit-Reset` and `Retry-After` headers returned to the client.
  2. If the traffic is legitimate, increase `RATE_LIMIT_WRITE_BURST` and `RATE_LIMIT_WRITE_REFILL_PER_S` for that route class or provide dedicated `X-API-Key` privileges.

### Symptom 2: Low Cache Hit Ratio ($< 50\%$)
- **Cause**: Bulk resume ingestion causing scan eviction, or TTL expiring prematurely.
- **Remedy**:
  1. Verify `SegmentedLRUCache` probation ratio (default 20%).
  2. Increase `CACHE_MAX_BYTES` to provide larger capacity for GitHub/parse entries.
  3. Check system clock monotonicity; ensure NTP sync has not caused wall-clock jumps.

### Symptom 3: Memory Climbing Toward Container Limit
- **Cause**: Large batch files or multiple concurrent PDF extractions.
- **Remedy**:
  1. `MemoryGuard` will trigger at `high_watermark` (80%) and evict to `low_watermark` (60%).
  2. Lower `MEMORY_RSS_SOFT_LIMIT_MB` to trigger proactive eviction earlier.
  3. Ensure `WORKER_PROCESSES` does not exceed available core count.

### Symptom 4: Upstream GitHub 403 / 429 Errors
- **Cause**: Unauthenticated IP quota exhausted (60 req/h).
- **Remedy**:
  1. Set `GITHUB_TOKEN` in `.env` to raise the limit to 5,000 req/h.
  2. `OutboundLimiter` automatically pauses requests until `x-ratelimit-reset`. Check `/metrics` under `outbound_throttled_total`.

---

## 5. Architecture Upgrade Path & "If I Had More Time"

### 1. Redis-Backed Distributed Layer
Currently, caches and token buckets are local to the Python process.
- **Upgrade Path**: When scaling to multi-instance Kubernetes clusters, deploy Redis Cluster to back `KeyedLimiter` (using Redis `CL.THROTTLE` or Lua token bucket scripts) and shared cache lookups.

### 2. TinyLFU / W-TinyLFU Eviction
- **Upgrade Path**: Plain LRU and Segmented LRU can be upgraded to Window-TinyLFU (like Java Caffeine). TinyLFU uses a 4-bit Count-Min Sketch to estimate admission frequency with negligible memory overhead, improving hit ratio by an estimated 5–10% on skewed zipfian distributions.

### 3. OpenTelemetry Distributed Tracing
- Integrate OpenTelemetry spans across the pipeline (parsing span, scoring span, upstream GitHub HTTP span) with trace propagation across async jobs.
