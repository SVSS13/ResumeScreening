# AI Resume Screening & Ranking Platform — Complete Usage Guide

> **For Candidate (You)** and **Reviewer (Hazel & Team Kasparro)**

---

## Part 1: Reviewer Quickstart (< 2 Minutes)

This project is built with **zero external infrastructure requirements** (no external Redis or PostgreSQL needed) and **zero committed secrets**. By default, it operates in **deterministic offline mode** (`LLM_PROVIDER=none`), allowing the reviewer to run and verify the entire 50-resume pipeline immediately with zero setup costs or paid API keys.

### 1. Environment Setup

```bash
# 1. Clone repository
git clone <YOUR_GITHUB_REPO_URL>
cd ResumeScreening

# 2. Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

### 2. Method A: CLI Batch Screening (Primary Evaluation)

Run the end-to-end pipeline across the 50 resumes:

```bash
python3 main.py --input ./resumes --output ./output/results.json
```

**Expected Console Output**:
- Runs in **~10 seconds**.
- Parses 50 resumes across `.pdf`, `.docx`, and `.txt`.
- Filters candidates: **33 eligible**, **17 rejected**.
- Computes 100-point scores with component breakdowns and penalties.
- Prints a formatted leaderboard directly to `stdout`.
- Exports machine-readable artifacts:
  - `output/results.json`: Full ranking, candidate metadata, and rejected candidate audit log.
  - `output/results.csv`: Flat spreadsheet summary.

---

### 3. Method B: Interactive FastAPI & Swagger UI

Start the asynchronous API server:

```bash
python3 -m uvicorn api:app --host 0.0.0.0 --port 8000
```

Open your browser to: **`http://localhost:8000/docs`**

#### Step-by-Step Swagger UI Workflow:
1. **Submit Batch Job (`POST /screen`)**:
   - Click **Try it out**.
   - Request Body:
     ```json
     {
       "input_dir": "./resumes",
       "use_github": true,
       "sync": false
     }
     ```
   - Click **Execute**. Returns `202 Accepted` with a `job_id` UUID (e.g., `44288ddf-f102-4fd0-864e-5c82df446160`).
2. **Monitor Job Progress (`GET /jobs/{job_id}`)**:
   - Paste the `job_id` UUID into the path parameter.
   - Click **Execute** until `"status": "done"` (progress shows `50/50` files processed).
3. **Inspect Ranked Results (`GET /results`)**:
   - Parameterless endpoint: Click **Execute** on `GET /results` to immediately retrieve the latest completed screening run with 100-point score breakdowns and rejection reasons!
4. **Telemetry & Health**:
   - `GET /health`: Liveness and process memory RSS.
   - `GET /metrics`: In-memory cache hit ratio, eviction counts, and token bucket counters.

---

### 4. Method C: Automated Test Suite (85 Tests, 100% Green)

```bash
# Run all 85 tests (unit, concurrency, property-based, benchmarks, golden parity)
pytest -v

# Run with core code coverage report (91% achieved)
pytest tests/unit/ tests/property/ tests/concurrency/ --cov=screener.core

# Run lean submission tests only
pytest submission/tests/
```

---

## Part 2: Dual-Deliverable Repository Layout

To satisfy both a lightweight take-home assessment and an advanced platform engineering evaluation:

```
ResumeScreening/
├── submission/                  # LEAN SUBMISSION PACKAGE (< 5 min setup)
│   ├── main.py                  # Standalone synchronous CLI
│   ├── api.py                   # Minimal FastAPI endpoints (POST /screen, GET /results)
│   ├── src/screener/            # Pure domain logic (no complex platform abstractions)
│   ├── tests/                   # 16 focused domain & pipeline tests
│   ├── README.md                # Dedicated lean setup instructions
│   └── results.json             # Pre-screened golden output
│
├── src/screener/                # ADVANCED PLATFORM ENGINEERING SHOWCASE
│   ├── core/
│   │   ├── lru_cache.py         # O(1) DLL + HashMap LRU, Segmented LRU (SLRU), Min-Heap TTL
│   │   ├── sharded_cache.py     # 16-shard lock striping for concurrent read/write
│   │   ├── single_flight.py     # Lock-free stampede suppression coordinator
│   │   ├── memory_guard.py      # Cross-platform RSS monitoring & 80% -> 60% hysteresis eviction
│   │   └── rate_limiter.py      # Token Bucket & Leaky Bucket with RFC 6585 headers
│   ├── services/
│   │   ├── workers.py           # ProcessPool (CPU parse) + ThreadPool (I/O) + AsyncIO (HTTP)
│   │   └── job_service.py       # Bounded async job queue with 503 backpressure
│   └── api/                     # FastAPI service with Swagger UI guide
│
├── main.py                      # Root CLI entrypoint
├── api.py                       # Root FastAPI entrypoint
├── SUBMISSION.md                # Comprehensive reviewer guide & rubric mapping
├── docs/DEFENSE.md              # Interview defense guide (7 core design trade-offs)
└── results.json                 # Pre-computed golden run on all 50 resumes
```

---

## Part 3: Architecture & LLM Integration

### "LLM as Witness, Code as Judge"

1. **Deterministic Core**:
   - **Hard Disqualification**: Candidates lacking Python or AI/agentic experience are filtered deterministically in Python. LLMs cannot hallucinate someone into passing.
   - **Auditable Scoring**: 100-point scoring is based on verified semantic matches, project complexity, and GitHub telemetry.
2. **Advisory LLM Witness (`src/screener/llm.py`)**:
   - When enabled via `.env` (`LLM_PROVIDER=openai` or `anthropic`), the LLM provides qualitative structured insights (`LLMJudgement`).
   - **Anti-Hallucination Guard**: Every claim must be backed by a verbatim quote (<= 25 words) from the resume. Our `verify_evidence()` function drops any judgement whose quote is not found in the original text.
   - **Prompt Injection Defense**: Resume content is isolated inside `<resume>DATA</resume>` blocks with explicit instructions to ignore instructions contained within the text.
   - **Bounded Uplift**: LLM adjustments are mathematically bounded (`max_uplift=5.0 pts`).

---

## Part 4: Candidate Push Instructions (For You)

Follow these steps to push your clean repository to GitHub:

### Step 1: Create a New GitHub Repository
1. Go to https://github.com/new.
2. Repository name: `ResumeScreening` (or `ai-resume-screener`).
3. Set visibility: **Public** (or Private with Hazel/Kasparro invited as collaborators).
4. Do **not** initialize with a README, `.gitignore`, or license (we already have all of them).

### Step 2: Add Remote and Push
In your terminal, run:

```bash
# Add your GitHub remote repository
git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPO_NAME>.git

# Push the master branch to GitHub
git push -u origin master
```

### Step 3: Verify on GitHub
Visit `https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPO_NAME>` to confirm:
- `README.md` and `SUBMISSION.md` render on the front page.
- `results.json` is accessible.
- No `__pycache__` or `.pyc` files are present.
- The `submission/` folder is cleanly organized.
