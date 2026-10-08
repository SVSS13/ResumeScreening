# ADR-003: Concurrency Architecture & OS Synchronization Model

## Status
Accepted

## Context
Python's CPython runtime is governed by the Global Interpreter Lock (GIL). Running CPU-intensive tasks (PDF parsing, text regex scanning, TF-IDF matrix computation) inside standard Python threads does not achieve multicore parallelism; in fact, thread switching overhead increases overall latency.
Conversely, running network I/O (GitHub API, LLM API) in processes introduces severe IPC serialization/pickling overhead.
Furthermore, the previous architecture called `asyncio.run()` synchronously inside `pipeline.py`, preventing clean integration with FastAPI's event loop.

## Decision

### 1. Three-Tier Runtime Partitioning (GIL-Aware)
We decompose execution across three specialized concurrency runtimes:
1. **CPU-Bound Tasks (`ProcessPoolExecutor`)**:
   - PDF extraction, docx parsing, regex feature scanning, and TF-IDF vector fitting.
   - Separate OS processes bypass the GIL and leverage all physical CPU cores.
   - Input paths and serialized extraction results are exchanged via IPC.
2. **I/O-Bound Async Tasks (`asyncio` Event Loop)**:
   - Outbound HTTP requests to GitHub and LLM providers via `httpx.AsyncClient`.
   - Single event loop handles thousands of concurrent socket states with negligible context switch cost.
3. **Blocking File I/O & Snapshots (`ThreadPoolExecutor`)**:
   - Cache snapshot persistence, disk writes, and atomic file replacements (`os.replace`).
   - Small bounded thread pool ($M = 4$) offloads disk waits from the main event loop.

### 2. Lock Striping (`ShardedLRUCache`)
To allow high-throughput multi-threaded access without coarse lock contention:
- Caches are partitioned into $N$ independent shards ($N=16$, default).
- Shard selection: $\text{shard\_index} = \text{hash}(\text{key}) \pmod N$.
- Each shard contains its own `LRUCache` instance and an independent `threading.Lock`.
- Threads accessing keys in different shards never contend for locks.

### 3. Strict Lock Ordering Rule & Deadlock Prevention
To formally eliminate deadlocks:
- **Rule 1**: A thread must hold at most **ONE** shard lock at any given time. Multi-shard acquisitions are strictly forbidden.
- **Rule 2**: Shard locks must **NEVER** be held while invoking external loaders, computing hashes, or performing network I/O.
- **Rule 3**: Cross-shard operations (such as aggregate stats or `MemoryGuard` sweeps) acquire and release shard locks sequentially one by one, never simultaneously.

### 4. Cache Stampede Suppression: Single-Flight
When a popular cache key misses or expires, concurrent threads must not simultaneously execute the expensive loader:
- `SingleFlight` coordinator maintains a mapping of `in_flight: dict[key, Future/Event]`.
- The first thread discovering a miss registers an in-flight entry and invokes the loader.
- Subsequent threads discover the existing entry and wait on the `Future`/`Event`.
- Once completed, all waiting threads receive the identical result; the entry is removed from `in_flight`.

## Alternatives Considered & Rejected
- **Pure Threading with GIL**: Rejected because PDF extraction took ~15s sequentially and threads showed 0% speedup under GIL contention.
- **Single Global Mutex**: Rejected because high concurrency causes thread starvation and serializes all cache lookups.

## Consequences
- **Positive**: Multicore parallelism for CPU work; low latency for async I/O; linear throughput scaling across cache shards; zero deadlocks.
- **Negative**: Inter-process communication (IPC) requires serializable inputs/outputs for `ProcessPoolExecutor` jobs, which is accommodated by clean dataclasses (`RawResume`, `ParsedResume`).
