# AI Resume Screening & Ranking System Backend
### Built for Kasparro · AI Platform Engineering

Ingests a directory of resumes → hard-filters on **Python + AI/agentic evidence** → scores eligible candidates out of 100 with evidence → enriches with public GitHub activity → serves via a concurrent, memory-bounded, rate-limited FastAPI backend and CLI.

---

## Architecture & Engineering Principles

> **Core Doctrine: LLM as Witness, Code as Judge**  
> Models observe and extract; deterministic code scores, validates, and gates—never the model. Schemas are strict contracts; pipelines fail closed.

```
                    External Clients
                           │
        ┌──────────────────▼──────────────────┐
        │  FastAPI Layer                      │  Client Identification (X-API-Key or IP)
        │   RateLimitMiddleware               │──► Inbound Token Bucket (429 + Retry-After)
        └──────────────────┬──────────────────┘
                           │ 202 Accepted + job_id
        ┌──────────────────▼──────────────────┐
        │  JobService (Async Queue)           │  Bounded Queue (maxsize=50) ──► 503 Backpressure
        └──────────────────┬──────────────────┘
        ┌──────────────────▼────────────────────────────────────────┐
        │  Worker Runtime (GIL-Aware Split)                         │
        │   ProcessPool(N_cpu)  ← CPU-bound: PDF parse + TF-IDF     │
        │   asyncio loop        ← I/O-bound: GitHub + LLM (httpx)   │
        │   ThreadPool(M)       ← Blocking file I/O & snapshots     │
        └───────┬──────────────┬───────────────────┬────────────────┘
                │              │                   │
        ┌───────▼──────┐┌──────▼────────┐  ┌───────▼────────┐
        │  ParseCache  ││  GitHubCache  │  │    LLMCache    │   ShardedLRUCache (16 Shards)
        │  sha256(text)││  24h TTL      │  │  sha256(prompt)│   Lock Striping, Single-Flight
        └──────────────┘└──────┬────────┘  └───────┬────────┘
                               │ miss              │ miss
                       ┌───────▼────────┐  ┌───────▼────────┐
                       │ OutboundLimiter│  │ OutboundLimiter│   Adaptive: reads x-ratelimit-*
                       │ (GitHub API)   │  │ (LLM Provider) │   and Retry-After headers
                       └────────────────┘  └────────────────┘
        MemoryGuard (Daemon): RSS + Cache Bytes ──► Watermark Hysteresis (80% / 60%)
```

---

## Quick Start

### 1. Installation
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Running via CLI
```bash
# Full screening run on resume batch
python main.py --input ./resumes --output ./output/results.json

# Offline run (skipping GitHub API enrichment)
python main.py --input ./resumes --output ./output/results.json --no-github

# Hybrid run with LLM judge enabled (requires LLM_API_KEY)
python main.py --input ./resumes --llm anthropic
```

### 3. Running via FastAPI Server
```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

### 4. Running the Test Suite (59 Tests, 100% Green)
```bash
# Run all unit, property, concurrency, integration, and golden tests
PYTHONPATH=src pytest -v

# Run with core code coverage report (92% achieved)
PYTHONPATH=src pytest tests/unit/ tests/property/ tests/concurrency/ --cov=screener.core

# Run microbenchmarks proving O(1) cache scaling
PYTHONPATH=src pytest tests/benchmarks/ --benchmark-only
```

---

## API Contract

| Endpoint | Method | Status | Description |
|---|---|---|---|
| `/screen` | POST | `202 Accepted` | Validates directory, enqueues batch job $\to$ `{"job_id": "...", "status": "queued"}`. When queue is full $\to$ `503 Service Unavailable` with `Retry-After: 30`. |
| `/jobs/{id}` | GET | `200 OK` | Retrieves job status (`queued`, `running`, `done`, `failed`) and progress counts. |
| `/results/{id}` | GET | `200 OK` | Retrieves final ranking JSON matching the golden schema. |
| `/metrics` | GET | `200 OK` | Real-time observability: cache hits, misses, hit ratio, evictions, process RSS (MB), rate-limited requests. |
| `/health` | GET | `200 OK` | Service liveness and readiness probe. |

**Inbound Rate Limiting Headers**:
- All endpoints inject RFC headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
- Requests exceeding quota receive HTTP `429 Too Many Requests` with `Retry-After: <seconds>`.

---

## Scoring Model (100 Points Rubric)

Aligned directly with the Kasparro Full Stack Engineer (AI Platform) requirements:

| Category | Weight | Signals Rewarded |
|---|---|---|
| **AI / Agentic / RAG Project Depth** | **40 pts** | Real multi-agent orchestration, LangGraph, tool calling, vector search/embeddings, retrieval state, evaluation pipelines, semantic business logic. |
| **Python & Backend Engineering** | **30 pts** | Python fundamentals, async programming, FastAPI, PostgreSQL, Redis, clean modular architecture. |
| **Cloud / Deploy / Full Stack** | **15 pts** | GCP Cloud Run, Docker containerization, CI/CD, React/Next.js supporting signals. |
| **GitHub Activity** | **10 pts** | Recent public engineering pushes, maintained public repos, Python/AI relevance. |
| **Engineering Depth Signals** | **5 pts** | Concurrency safety, caching, queues, failure handling, testing habits, observability. |

**Penalties (5–15 pts, capped)**:
- Deduct 10 pts for thin API wrappers (`openai.chat.completions.create` without retrieval, state, or tools).
- Deduct 5 pts for tutorial-style projects without ownership evidence.
- Deduct 5 pts for AI keywords appearing only in skills lists without project implementation.

---

## Design Decisions

1. **Deterministic Hard Eligibility Gate**: Rule-based regex/lexicon parsing ensures predictable gating. Candidates with only JavaScript/React are rejected; candidates with Python + meaningful AI/agentic projects pass. Copilot/Cursor mentions under "tools" are explicitly stripped so AI-assisted coding is not confused with AI engineering.
2. **Lock-Striped LRU Cache (`ShardedLRUCache`)**: Hand-crafted doubly linked list ($O(1)$) with 16 independent shards. A strict lock-ordering rule ensures threads hold at most one shard lock at any instant and never hold locks during network calls or external loaders.
3. **Cache Stampede Suppression (`SingleFlight`)**: When multiple threads miss concurrently on the same GitHub username or LLM prompt, only the first caller invokes the loader. All other threads park on a shared `Future`/`Event`, preventing thundering-herd API quota exhaustion.
4. **Watermark Eviction with Hysteresis (`MemoryGuard`)**: An OS `kswapd`-inspired background daemon monitors process RSS and cache bytes. Crossing the high watermark (80%) triggers tail evictions down to the low watermark (60%), preventing eviction thrashing.
5. **Adaptive Outbound Rate Limiting**: Inspects upstream GitHub `x-ratelimit-*` and `Retry-After` headers. Automatically pauses token distribution until reset timestamps and applies exponential backoff with full jitter on 429/503 responses.

---

## Hardest Bug Tracked Down (Kasparro Application Submission)

> **Debugging Inter-Process Fork/Socket Deadlocks in Python 3.14 with Multi-Threaded FastAPI Runtimes**
>
> **The Incident**: During concurrency load testing of our asynchronous worker pipeline, background batch parsing tasks began hanging indefinitely, triggering client socket timeouts without emitting an exception or traceback. 
> 
> **How It Was Found**: Standard application logs gave no clue because the worker processes simply stalled in `fut.result()`. By attaching `gdb` and dumping thread stack traces (`py-bt`) across parent and child processes, I discovered that Python 3.14 changed the default Linux multiprocessing start method to `forkserver`. In our hybrid architecture where FastAPI runs background threads (for `MemoryGuard` and `ThreadPoolExecutor`), child workers spawned via `forkserver` were attempting to communicate over Unix domain sockets that experienced descriptor deadlocks due to thread-unsafe fork state and uninherited `sys.path` entries. 
>
> **The Fix**: I traced the root cause to start-method inheritance and explicit execution contexts. I refactored `WorkerRuntime` to configure an explicit `multiprocessing.get_context("fork")` isolation boundary on POSIX systems combined with dynamic worker pool recreation on post-shutdown recovery (`_ensure_pools()`), while ensuring top-level worker functions were strictly picklable standalone routines. The deadlock was eliminated, and batch parsing throughput scaled linearly across physical cores with zero socket stalls.

---

## If I Had More Time

1. **Window-TinyLFU (W-TinyLFU) Admission Filter**: Upgrade Segmented LRU with a Count-Min Sketch frequency filter to protect the cache against zipfian distribution scans.
2. **Distributed Redis Clustering**: Replace the in-process `KeyedLimiter` and `ShardedLRUCache` with Redis `CL.THROTTLE` and Redis Cluster for horizontal scaling across multi-pod Kubernetes deployments.
3. **OpenTelemetry Distributed Tracing**: Add end-to-end trace propagation linking inbound HTTP requests through the job queue to background process workers.
