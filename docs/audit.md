# Baseline Audit & Findings Triage (Phase 1)

This document establishes the empirical ground truth for the AI Resume Screening & Ranking Platform. Every number recorded here was directly measured by executing tests, benchmarks, and profiling runs on the active repository.

---

## 1. Claimed vs. Measured Baseline Metrics

| Metric | Claimed (Previous Session) | Measured (Empirical Ground Truth) | Status / Delta |
|---|---|---|---|
| **Test Suite Pass Rate** | 73 passed / 0 failed | **73 passed / 0 failed** in 20.91 s (`pytest`) | Exact match (100% green) |
| **`screener.core` Coverage** | 92% | **91.8%** (815 / 888 statements) | Verified ($\ge 90\%$ met) |
| **Project-wide Coverage** | Unstated (assumed ~90%) | **86%** (1,988 / 2,314 statements across `src/screener`) | **Deficit found**: `adapters/` is at 0%, `report.py` at 0% |
| **LRU Cache Get Hit Ops** | ~2.5M ops/s | **2.26M ops/s** (median 430.6 ns per hit) | Verified microbenchmark |
| **LRU Cache O(1) Put Scaling** | ~747 µs (1k) vs ~739 µs (10k) | **817.8 µs (1k) vs 823.3 µs (10k)** | **Verified flat $O(1)$**: 0.7% variance over 10× keys |
| **Sequential Pipeline Runtime** | Reported ~15 s / 50 resumes | **9.80 s** (Cold RSS: 267.4 MB) | Measured baseline on 50 resumes |
| **Parallel Worker Runtime** | Reported ~2.07 s via API | **3.28 s** Cold, **3.15 s** Warm (RSS: 268.5 MB) | **3.0× speedup** over sequential baseline |
| **Golden Candidate Split** | 33 eligible / 17 rejected | **33 eligible / 17 rejected** | 100% deterministic parity |
| **Docker Build / Run** | Unverified | **Daemon running** (`Docker 28.5.2`); image unbuilt | Pending verification in Phase 3 |

---

## 2. Test Coverage Breakdown by Subsystem

Measured via `pytest --cov=src/screener --cov-report=term-missing`:

| Subsystem / Module | Statements | Missed | Coverage | Weakest Areas / Missing Lines |
|---|---|---|---|---|
| `screener.core` | 888 | 73 | **91.8%** | `memory_guard.py` (80%), `lru_cache.py` (91%) |
| `screener.adapters` | 89 | 89 | **0.0%** | `cache_adapter.py` (0%), `outbound_client.py` (0%) |
| `screener.services` | 247 | 42 | **83.0%** | `workers.py` (81%), `job_service.py` (85%) |
| `screener.api` | 198 | 20 | **89.9%** | `deps.py` (81%), `app.py` (89%) |
| `screener.domain / pipeline` | 892 | 102 | **88.6%** | `report.py` (0%), `llm.py` (75%), `ingest.py` (81%) |
| **Total `src/screener`** | **2,314** | **326** | **85.9%** | Needs targeted adapter & service unit tests to guarantee $\ge 85\%$ across all components |

---

## 3. Triage of Audit Findings A1–A10

### Finding A1: Golden Test Scope & "Byte-Identical" Claims
- **Status:** Confirmed
- **Severity:** Major
- **Evidence:** `tests/golden/test_golden_ranking.py:13-55`.
  The test compares candidate fields and count of rejections, but docstrings claim "identical ranking output" without clarifying that non-deterministic runtime fields (`runtime_seconds`, execution timestamps) are intentionally excluded. Furthermore, individual `rejected_candidates` rejection reasons are not asserted.
- **Required Fix:** Update docstrings and add assertions for `rejected_candidates` item schemas (rejection reason, candidate name) while explicitly documenting the exclusion of elapsed time fields.

### Finding A2: Lock Hierarchy & Loader Execution Under Lock
- **Status:** Confirmed
- **Severity:** Blocker
- **Evidence:** `src/screener/core/sharded_cache.py:82-103`.
  In `ShardedLRUCache.get_or_load()`, line 89 acquires and releases the shard lock for the fast-path check. In the slow path, `_load_and_cache` invokes `loader()` outside the shard lock, acquiring the shard lock only upon insert.
- **Required Fix:** While the design intends for `loader()` to execute outside the lock, we must formalize this with explicit assertions and a multi-threaded stress test with strict timeouts proving no thread ever holds two shard locks or holds a lock during I/O.

### Finding A3: `SingleFlight` Thread-Safety & Context Manager Anti-Pattern
- **Status:** Confirmed
- **Severity:** Blocker
- **Evidence:** `src/screener/core/single_flight.py:34-47`.
  `SingleFlight.execute` contains an anti-pattern: inside `with self._lock:`, it calls `self._lock.release()`, executes `call.event.wait()`, and in `finally:` calls `self._lock.acquire()`. Manually manipulating locks inside a `with` context manager can corrupt lock state or deadlock if exceptions occur during re-acquisition.
- **Required Fix:** Refactor `SingleFlight.execute` to cleanly release the mutex before awaiting the event. Provide thread-safe coordination for mixed event loop and thread-pool execution, validated by a 32-thread × 100k-op stress test.

### Finding A4: `MemoryGuard` Cross-Platform Portability
- **Status:** Confirmed
- **Severity:** Major
- **Evidence:** `src/screener/core/memory_guard.py:33-47`.
  `get_process_rss_mb()` assumes Linux `ru_maxrss` unit conventions (KiB). On macOS (Darwin), `ru_maxrss` is in bytes (requiring division by $1024^2$). On Windows, `resource` is unavailable. If `psutil` is missing on Windows/macOS, `/health` could return inaccurate data or fail.
- **Required Fix:** Implement platform-aware RSS inspection (`sys.platform` branching: Linux `/proc` or KiB, Darwin bytes, Windows fallback to `psutil` or `0.0` with warning). Ensure `/health` never throws.

### Finding A5: README Incident Narrative Authenticity
- **Status:** Confirmed
- **Severity:** Minor / Documentation
- **Evidence:** `README.md:130-139`.
  The README currently features a writeup on debugging Python 3.14 fork/socket issues. While technically authentic to this migration, replacing it with the candidate's real production incident (e.g., Qr-Connect session expiry race condition with PostgreSQL row-level locking) ensures 100% interview integrity.
- **Required Fix:** Replace with the real incident narrative or remove per user preference.

### Finding A6: Test Coverage Scope (Adapters at 0%)
- **Status:** Confirmed
- **Severity:** Major
- **Evidence:** `src/screener/adapters/cache_adapter.py:6-119` (0%), `src/screener/adapters/outbound_client.py:2-34` (0%), `src/screener/report.py:2-53` (0%).
  While `screener.core` is at 91.8%, `adapters` has zero unit tests.
- **Required Fix:** Add dedicated unit tests for `cache_adapter.py`, `outbound_client.py`, and `report.py` to bring overall project coverage to $\ge 88\%$.

### Finding A7: Docker Image Verification
- **Status:** Partially True (Docker available, image untested)
- **Severity:** Major
- **Evidence:** `Dockerfile:1-42`. Docker daemon is running (`Docker version 28.5.2`), but the build and container `/health` check have not been executed live.
- **Required Fix:** Execute `docker build`, run the container, curl `/health`, and log real terminal output.

### Finding A8: Performance Claims Verification
- **Status:** Confirmed (Empirically Measured)
- **Severity:** Minor
- **Evidence:** Microbenchmarks show 2.26M gets/s. Real end-to-end runs confirm 9.80 s sequential vs 3.28 s parallel (3.0× speedup) for 50 resumes with 268 MB RSS.
- **Required Fix:** Update documentation with these verified empirical numbers.

### Finding A9: Packaging Separation (Graded Brief vs Advanced Platform)
- **Status:** Confirmed
- **Severity:** Blocker
- **Evidence:** Root repository combines both the simple graded take-home requirement and the advanced distributed platform. A reviewer evaluating the brief wants a zero-friction CLI run without platform complexity.
- **Required Fix:** Create a clean `submission/` directory containing the standalone, lean take-home CLI, domain files, results, and focused tests (< 5 min setup). Retain the advanced platform layer in `src/screener/` and root.

### Finding A10: Upstream GitHub Quota & Outbound Limiter
- **Status:** Confirmed
- **Severity:** Major
- **Evidence:** `src/screener/core/rate_limiter.py:270-280` & `src/screener/github_enrich.py:126-128`.
  In `OutboundLimiter.observe`, when `x-ratelimit-reset` is present, it does not calculate `reset_ts - time.time()`, but instead hardcodes `now + 10.0`.
- **Required Fix:** Correctly calculate the pause duration as `max(0.0, reset_ts - time.time())`. Add test with simulated HTTP transport verifying `rate_limited` status while batch completes.

---

## 4. Fix List for Approval (Gate 1)

1. **A2 & A3 (Concurrency Correctness)**: Refactor `SingleFlight` mutex coordination, add 32-thread × 100k-op stress test and lock-free loader assertion.
2. **A4 (Platform Portability)**: Make `MemoryGuard` platform-aware across Linux, macOS, and Windows.
3. **A1 & A10 (Golden & Quota Hardening)**: Clarify golden test docstring/assertions; fix epoch reset math in `OutboundLimiter` with mock transport tests.
4. **A6 (Multi-Module Coverage)**: Add unit tests for `adapters/` and `report.py` to exceed 85% project-wide coverage.
5. **A5 (README Integrity)**: Update incident narrative with real production story.
6. **A7 (Docker Verification)**: Build and test container live.
7. **A9 (Packaging)**: Construct self-contained `submission/` folder.
