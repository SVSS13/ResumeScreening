# ADR-005: Job Service Architecture, Backpressure & Graceful Shutdown

## Status
Accepted

## Context
In the baseline implementation, `POST /screen` processed the entire batch synchronously on the calling HTTP thread. If a request was submitted while another batch was running, threads stacked up, leading to connection timeouts and memory spikes.
Furthermore, if the server was killed (e.g., during deployment or restart), active work was aborted, and cache files risked corruption.

## Decision

### 1. Bounded Asynchronous Job Model
We implement an asynchronous Job Service (`JobService`):
- `POST /screen` validates the request, instantiates a `job_id = uuid4()`, enqueues the job descriptor, and immediately returns HTTP 202 Accepted.
- The underlying queue is an `asyncio.Queue` with a fixed upper bound (`queue_max_size = 50`).

### 2. Producer-Consumer Backpressure
When inbound submissions arrive faster than the workers can process:
- If `queue.qsize() >= queue_max_size`, `JobService` rejects new submissions immediately with **HTTP 503 Service Unavailable** and an advisory `Retry-After: 30` header.
- This creates backpressure, forcing clients or upstream load balancers to back off rather than letting internal buffers grow without bound.

### 3. In-Memory Bounded Job Store
- Job states (`queued`, `running`, `done`, `failed`) and final result payloads are stored in an in-memory `LRUCache[str, JobRecord]` bounded to 1,000 entries with a 24-hour TTL.
- Eliminates memory leaks from forgotten completed jobs while obeying the strict "No external DB" requirement.

### 4. Graceful Shutdown & Atomic Persistence
When `SIGTERM` or `SIGINT` signals are intercepted:
1. **Stop Ingress**: The API marks the service as draining; subsequent `POST /screen` requests receive HTTP 503.
2. **Drain Queue**: The service allows actively running jobs to finish, waiting up to `shutdown_timeout_s` (default 15s).
3. **Atomic Snapshot**: Caches persist their entries to a temporary file (`cache.json.tmp`), flush/sync to disk, and atomically overwrite the primary path using `os.replace()`. This prevents file corruption even if a hard kill occurs.
4. **Join Pools**: Executors (`ProcessPoolExecutor`, `ThreadPoolExecutor`) are cleanly shut down without abrupt thread or process termination.

## Alternatives Considered & Rejected
- **Unbounded In-Memory Queue**: Rejected because queuing hundreds of resume batches causes RAM exhaustion.
- **Direct Synchronous Processing**: Rejected because HTTP worker threads block for 15+ seconds per batch.

## Consequences
- **Positive**: Responsive HTTP API; immediate client feedback with progress tracking; backpressure protection; zero corrupted cache files.
- **Negative**: Clients must poll `GET /jobs/{id}` or `GET /results/{id}` to retrieve final rankings.
