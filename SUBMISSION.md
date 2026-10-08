# SDE Intern Coding Assignment — Submission Guide
## Project: AI Resume Screening & Ranking System

---

## 1. Executive Summary
This submission implements a production-minded **AI Resume Screening & Ranking System** designed to evaluate candidate resumes for an SDE Internship requiring strong Python engineering fundamentals and practical exposure to AI/agentic systems.

- **Primary Language**: Python 3.12 / 3.14
- **Interfaces**: Both **CLI** and **FastAPI / OpenAPI** supported out of the box.
- **Dependencies**: Standard library first; zero external database or Redis requirements.
- **Dataset Evaluated**: 50 candidate resumes (`./resumes`).
- **Outcome**: **33 eligible candidates ranked** by score; **17 rejected candidates** with explicit disqualification reasons.
- **Verification**: **85 automated tests passing (100% green)**, 91% code coverage, and sub-4-second batch execution.

---

## 2. Reviewer Quickstart (< 2 Minutes)

### Step 1: Environment Setup
```bash
# 1. Clone repository and navigate to directory
cd ResumeScreening

# 2. Create and activate a clean virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Step 2: Run via CLI (Primary Interface)
To run the screening pipeline on the 50 resumes and generate the ranked JSON output:
```bash
python3 main.py --input ./resumes --output ./output/results.json
```
*Output*: Generates `./output/results.json` and prints a formatted terminal leaderboard to `stdout`.

### Step 3: Run via FastAPI / Swagger UI (Interactive Interface)
```bash
# Start the API server
uvicorn api:app --host 0.0.0.0 --port 8000
```
- Open Swagger UI in your browser: **`http://localhost:8000/docs`**
- **Test in 3 clicks**:
  1. Click **`POST /screen`** $\to$ *Try it out* $\to$ *Execute* (returns HTTP 202 with `job_id`).
  2. Click **`GET /jobs/{job_id}`** $\to$ *Execute* (poll until `status == "done"`, ~2–3 seconds).
  3. Click **`GET /results`** (or `GET /results/{job_id}`) $\to$ *Execute* (returns full ranked JSON).

### Step 4: Run the Test Suite
```bash
pytest
```
*Runs all 85 unit, concurrency, property-based (Hypothesis), integration, and golden tests.*

---

## 3. Reviewer FAQ: Secrets & LLM Usage

### Q: Why are there no API keys in this submission?
**Security Invariant:** Personal API keys and secrets must **never** be committed to version control. Doing so is a security vulnerability and violates production best practices. A clean `.env.example` is provided as the configuration template.

### Q: How can the reviewer evaluate the project without providing API keys?
**1. Zero-Secret Offline Execution (Default Mode):**
The system is intentionally engineered with a robust, deterministic offline engine:
- If no `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GITHUB_TOKEN` is supplied, the system automatically runs with `LLM_PROVIDER=none`.
- Hard eligibility checks, TF-IDF semantic scoring, lexical depth matching, and penalties run in pure Python without making external paid API calls.
- GitHub lookups use public rate-limited endpoints or cached disk responses in `.cache/github.json`.
- The reviewer can run the entire pipeline immediately upon cloning with **zero setup, zero credentials, and zero cost**.

**2. Pre-Generated Golden Results (`results.json`):**
The pre-screened output for all 50 resumes is bundled directly in [`results.json`](results.json), allowing reviewers to inspect the final ranked candidates, score breakdowns, and rejection audit logs immediately.

**3. Optional LLM Verification (If the reviewer wants to test LLM scoring):**
If you wish to test the LLM structured output feature, create a `.env` file with your preferred provider:
```bash
# In .env:
LLM_PROVIDER=openai           # or "anthropic"
LLM_API_KEY=your-api-key-here
LLM_MODEL=gpt-4o-mini         # or "claude-haiku-5-5"
```
When configured, `llm.py` automatically validates LLM judgements against the Pydantic schema `LLMJudgement`, requiring verbatim quotes from the resume as evidence before applying advisory adjustments.

---

## 4. Pipeline & Requirements Compliance

### 1. Ingestion & Format Support (§2)
- **Supported Formats**: `.pdf` (`pdfplumber` with `pypdf` fallback), `.docx` (`python-docx`), and `.txt`.
- **Fault-Tolerant Parsing**: Malformed, password-protected, or corrupted resumes are logged to `failed_files` without interrupting batch processing.

### 2. Hard Eligibility Filter (§3)
- **Rule-Based & Deterministic**: A candidate must demonstrate both:
  1. **Python Evidence**: Genuine skills, project stack, work experience, or implementation code. Pure Java/JS/React profiles are disqualified.
  2. **AI / Agentic Evidence**: At least one meaningful project or framework (e.g., LangChain, LangGraph, RAG pipelines, vector search, tool-calling agents, multi-agent workflows).
- **Audit Logging**: Disqualified candidates output explicit `rejection_reasons` (e.g., `["No evidence of Python stack", "No AI/agentic project evidence"]`).
- **Result on 50 Resumes**: Exactly **33 eligible** and **17 rejected**.

### 3. 100-Point Candidate Ranking Model (§4)
Eligible candidates are scored across 5 categories:
| Category | Max Points | Evaluation Signals |
|---|---|---|
| **AI / Agentic Project Depth** | 40 | RAG architectures, multi-agent coordination, vector embeddings, state management, tool calling. |
| **Python & Backend Engineering** | 30 | FastAPI, asynchronous programming, PostgreSQL, Redis, backend architecture. |
| **Cloud / Deployment / Full Stack** | 15 | Docker, GCP, CI/CD, deployment pipelines, supporting React/Next.js signals. |
| **GitHub Activity** | 10 | Recent commits/events (0–5 pts) + maintained/relevant repositories (0–5 pts). |
| **Engineering Depth Signals** | 5 | Unit testing, concurrency, caching, failure handling, observability. |

**Penalty Deductions**:
- **-5 to -15 points**: Deducted for shallow API wrappers (e.g., calling `openai.ChatCompletion.create` without retrieval, state, or product logic).
- **-5 to -10 points**: Deducted for generic tutorial projects without evidence of ownership.

### 4. Public GitHub Enrichment (§5)
- Extracts public GitHub handles from resume header links.
- Evaluates recent activity (last push date) and maintained repositories.
- **Fail-Safe**: If the GitHub API returns HTTP 403/429 (unauthenticated quota of 60 req/h reached) or a profile is missing/private, screening continues normally, recording `status="rate_limited"` or `"not_found"` without crashing.

---

## 5. Design Decisions

1. **"LLM as Witness, Code as Judge"**:
   - LLMs can hallucinate or grade non-deterministically. Therefore, hard eligibility criteria and core ranking weights are implemented as deterministic, auditable Python algorithms.
   - Any LLM integration acts purely as an advisory witness providing structured evidence, validated against strict Pydantic schemas.
2. **Fail-Closed Architecture**:
   - Incomplete or malformed data is rejected explicitly with audit trails rather than failing silently or receiving unearned baseline scores.
3. **Standard-Library First Platform Architecture**:
   - To adhere to the time box and the constraint against heavy external infrastructure (no Redis, Memcached, or Celery), all caching and rate-limiting components are implemented in pure Python:
     - $O(1)$ Doubly-Linked List + Hash Map LRU Cache with Segmented LRU (SLRU) and Min-Heap TTL.
     - Single-Flight coordinator to suppress cache stampedes.
     - Token bucket inbound rate limiter with RFC 6585 headers.
     - Background `MemoryGuard` daemon enforcing $80\% \to 60\%$ watermark hysteresis eviction.

---

## 6. If I Had More Time

1. **Window-TinyLFU (W-TinyLFU) Admission Filter**:
   Augment the Segmented LRU cache with a Count-Min Sketch frequency filter to protect the cache from recency pollution during large batch scans.
2. **Distributed Redis Clustering**:
   For multi-pod Kubernetes deployments, replace the in-process `ShardedLRUCache` and `KeyedLimiter` with Redis Cluster and `CL.THROTTLE`.
3. **OpenTelemetry Distributed Tracing**:
   Add trace propagation linking inbound API requests to background worker processes and upstream GitHub calls.
4. **Enhanced OCR for Scanned PDFs**:
   Integrate Tesseract OCR as a secondary fallback for purely image-based PDF resumes lacking extractable text streams.

---

## 7. Deliverables Checklist

- [x] **Source Code**: Fully modularized codebase in `src/screener/`.
- [x] **CLI Entrypoint**: `main.py` runnable via `python main.py --input ./resumes --output ./output/results.json`.
- [x] **FastAPI Entrypoint**: `api.py` runnable via `uvicorn api:app` with Swagger UI.
- [x] **Configuration Template**: `.env.example` detailing all configurable environment variables.
- [x] **Dependencies**: `requirements.txt` with minimal, pinned package versions.
- [x] **Pre-Screened Output**: `results.json` generated for all 50 candidate resumes.
- [x] **README.md**: Complete documentation including setup, **Design Decisions**, and **If I Had More Time**.
- [x] **Automated Tests**: 85 passing tests covering unit, concurrency, property-based, and golden parity suites.
