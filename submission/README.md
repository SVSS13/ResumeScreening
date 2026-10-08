# AI Resume Screening & Ranking System — SDE Intern Submission

A production-minded, explainable backend that ingests a directory of ~50 candidate resumes, filters candidates against hard Python and AI/agentic eligibility requirements, scores eligible profiles across engineering depth dimensions, enriches scores with public GitHub activity, and produces an auditable ranked shortlist.

---

## Quickstart (< 2 Minutes)

### 1. Installation
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run via CLI (Primary Interface)
```bash
python main.py --input ./resumes --output ./output/results.json
```
- Reads PDF, DOCX, and TXT resumes from `--input`.
- Generates machine-readable ranked output at `--output`.
- Prints a formatted leaderboard summary to stdout.

### 3. Optional: Run via FastAPI
```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```
- Interactive docs available at `http://localhost:8000/docs`.
- `POST /screen`: Submit resume folder.
- `GET /results`: Fetch ranked candidates JSON.

### 4. Run Tests
```bash
pytest tests/
```

---

## Note on Secrets & LLM Evaluation

- **No Secrets Committed**: Following production security best practices, no personal API keys or GitHub tokens are hardcoded or committed to git.
- **Zero-Secret Offline Baseline**: By default, the system runs with `LLM_PROVIDER=none`. The entire ingestion, hard filtering, TF-IDF semantic scoring, and ranking pipeline runs 100% locally in pure Python without requiring any external API keys or paid services.
- **Pre-Generated Results**: The pre-computed screening output for the 50 resumes is bundled in [`results.json`](results.json).
- **Optional LLM Scoring**: If you wish to test with an LLM, copy `.env.example` to `.env` and set `LLM_PROVIDER=openai` (or `anthropic`) with your `LLM_API_KEY`.

---

## Design Decisions

### 1. Ingestion & Failure Isolation
- **Multi-Format Support**: Ingests PDF (`pdfplumber` with `pypdf` fallback), DOCX (`python-docx`), and TXT resumes.
- **Fail-Closed Processing**: Corrupted or unreadable files are caught and logged to `failed_files` with specific error reasons. A single corrupt document never crashes the batch.
- **Duplicate Detection**: Fast $O(1)$ Bloom filter checks paired with exact content hashing ensure duplicate submissions are cleanly skipped.

### 2. Hard Eligibility Filtering
- **Rule-Based Integrity**: Evaluated deterministically in pure Python before scoring.
  1. **Python Evidence**: Requires genuine Python usage in skills, projects, or work history (pure Java/JavaScript profiles are disqualified).
  2. **AI / Agentic Evidence**: Requires at least one concrete AI/LLM/RAG/agentic project or framework (e.g., LangChain, LangGraph, RAG pipelines, vector search, tool-calling agents).
- Disqualified candidates are rejected with explicit, human-readable reasons in `rejection_reasons`.

### 3. 100-Point Scoring Model
- **Weight Distribution**:
  - **AI / Agentic Project Depth (40 pts)**: RAG systems, tool use, retrieval, state orchestration.
  - **Python & Backend Engineering (30 pts)**: FastAPI, async programming, PostgreSQL, Redis.
  - **Cloud / Deployment / Full Stack (15 pts)**: Docker, GCP, deployment pipelines.
  - **GitHub Activity (10 pts)**: Recency of pushes (0–5 pts) and maintained repositories (0–5 pts).
  - **Engineering Depth Signals (5 pts)**: Testing, architecture, caching, concurrency.
- **Shallow Wrapper Penalties**: Deducts 5–15 points for thin API wrappers that lack retrieval, state, or product logic.

### 4. GitHub Enrichment
- Scrapes GitHub handles from resume links and queries public GitHub user and repo endpoints.
- If rate-limited or missing, screening completes without failing, recording `status="rate_limited"` or `"not_found"`.
- Token read strictly from `GITHUB_TOKEN` environment variable.

---

## If I Had More Time

1. **Window-TinyLFU (W-TinyLFU) Admission Filter**:
   Upgrade in-memory cache eviction with a Count-Min Sketch frequency filter to prevent scan pollution during multi-thousand resume batches.
2. **Distributed Redis Task Queuing**:
   Replace the in-process queue with Redis and Celery/BullMQ to distribute parsing workers across Kubernetes nodes.
3. **OCR Fallback for Scanned Resumes**:
   Incorporate Tesseract OCR for resumes submitted as flat scanned images without selectable text layers.
4. **OpenTelemetry Observability**:
   Instrument the pipeline with distributed traces capturing per-resume parsing, scoring, and enrichment latency.
