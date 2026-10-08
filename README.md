# AI Resume Screening & Ranking System

Ingests a folder of resumes → hard-filters on **Python + AI/agentic evidence** → scores eligible candidates
out of 100 with evidence → enriches with public GitHub activity → writes a ranked, explainable `results.json`.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: GITHUB_TOKEN, LLM_PROVIDER/LLM_API_KEY

python main.py --input ./resumes --output ./output/results.json          # full run
python main.py --input ./resumes --no-github                             # offline run
python main.py --input ./resumes --llm anthropic                         # hybrid (needs LLM_API_KEY)
uvicorn api:app                                                           # optional: POST /screen, GET /results
pytest -q                                                                 # 24 tests
```
Outputs: `output/results.json` (primary), `output/results.csv` (flat), terminal top-N table.

## Architecture
```
 resumes/*.pdf|docx|txt
        │
 [ingest.py]  pdfplumber (+pypdf fallback), docx, txt · extracts hyperlink annotations
        │      └─ unreadable / scanned / corrupt  ───────────────►  failed_files[]   (batch continues)
 [dedup.py]   Bloom filter (content-hash + e-mail) + exact confirm ─►  duplicate_files[]
        │
 [parse.py]   name · email · GitHub user (link annotations first) · sections · project/experience BLOCKS
        │
 [features.py + lexicon.py]  skill scan, tracking WHERE each skill appears: project block vs skills list
        │                     ("AI-assisted dev", Copilot, Cursor phrases are stripped first)
 [eligibility.py]   HARD, deterministic rules (no LLM)  ──► rejected_candidates[] with reasons
        │  Python evidence AND AI/LLM/RAG/agentic evidence
        │
 [tfidf.py]   TF-IDF fitted on ALL blocks of the batch → semantic depth / thin-wrapper similarity
        │
 [scoring.py] deterministic 100-pt rubric + penalties  ─┐
 [github_enrich.py] async, bounded, cached, rate-limit-aware (0-10)  ├─► [pipeline.py] rank → report.py
 [llm.py]     optional structured judge (Pydantic), evidence-verified ┘
```
| Module | Responsibility |
|---|---|
| `config.py` | weights, penalties, thresholds, prototypes, API settings (no secrets) |
| `lexicon.py` | all vocabulary/regex; change here to change what counts as evidence |
| `models.py` | Pydantic contracts incl. the LLM output schema |
| `pipeline.py` | orchestration + per-file failure isolation |

## Scoring (100 pts) – all evidence-based
| Category | Pts | How |
|---|---|---|
| AI/agentic/RAG depth | 40 | per AI block: base (framework 6 / LLM-only 3) + 3.5 × depth dimensions (retrieval, orchestration/state, tools, evaluation, data-processing, production) + TF-IDF semantic points (≤8). Resume = best block + 0.2·2nd + 0.1·3rd |
| Python & backend | 30 | Python in skills (3) + in project/role blocks (6 each, ≤2); FastAPI 5 / Flask-Django 3; async 3; PostgreSQL 3; Redis 3; REST/JWT/etc ≤2. Skills-list-only gets ~⅓ credit |
| Cloud / deploy / full-stack | 15 | GCP 4 (AWS/Azure 2.5), Docker 4, K8s 1.5, CI/CD 1, deployment evidence 2, React/Next.js 2 (supporting) |
| GitHub | 10 | recency of latest push (0-5) + maintained repos (0-3) + Python/AI-relevant repos (0-2) |
| Engineering depth | 5 | 1 pt per category in project text: testing, architecture, caching, queues, observability, concurrency, failure handling |

**Penalties (5-15, capped):** thin LLM wrapper (-10 no structural depth / -5 single signal & no framework),
tutorial-style/no ownership verbs (-5), AI only in skills list (-5). Applied to the total and listed in output.

## Design Decisions
**Filtering strategy.** Pure regex/lexicon rules, no LLM, so the gate is predictable and unit-tested. Evidence is
location-aware: Python mentioned only under *Education/Coursework* does not count; "AI-assisted development",
Copilot, Cursor do not count as AI engineering. Strong AI terms (LangChain, LangGraph, RAG, vector DBs, embeddings,
tool-calling, MCP, agents…) or LLM-API usage inside a project/role make a candidate eligible. AI keywords appearing
*only* in a skills list pass eligibility (the brief says framework mentions count) but are penalised and capped at
6 AI points. Classical ML/CV-only profiles (sklearn, CNN, XGBoost) are rejected because the role asks for
AI/LLM/agentic work; flip `accept_classical_ml` in `config.py` to change that policy. JS/Java/React in addition to
Python+AI is never a reason to reject.

**Scoring strategy.** A transparent rubric; each number is traceable to evidence snippets in the output. The
"project block" (title + bullets of an experience/project entry) is the unit of evidence, so a keyword in the skills
section earns far less than the same keyword used in a described project. Depth is measured by *dimensions*
(retrieval, orchestration/state, tools, evaluation, data-processing, production), which is what separates an agentic
RAG system from `openai.chat.completions.create()` in a Flask route.

**TF-IDF.** Fitted on every block in the batch, so words common to all resumes ("python", "developed") have low
IDF and rare technical terms dominate. Each block is compared (cosine) with an "ideal agentic/RAG project" prototype
and a "thin wrapper" prototype (`config.py`); `depth − 0.6·thin` becomes ≤8 semantic points. It also yields
`jd_similarity` (resume vs job description) used **only as a tie-breaker** and for explainability.

**Bloom filter.** Used for duplicate-resume detection (content hash + e-mail) in O(1)/constant memory. At 50 files a
set would do; it is here to show the design scales to large batches. Positives are confirmed against an exact map, so
false positives can never drop a real candidate.

**LLM usage (optional, hybrid).** Off by default; the system is fully functional without it. When enabled
(`LLM_PROVIDER`), a provider adapter (`llm.py`, only file with provider code) returns a Pydantic-validated
`LLMJudgement` (project kind, depth 0-10, verbatim evidence quote). Quotes are verified against the resume text
(hallucinated evidence is dropped), the resume is passed as untrusted data (prompt-injection guard), and the
LLM can only blend ≤40% of `ai_project_depth` and add ≤8 points over the deterministic score. Retries with backoff,
bounded concurrency, and a failure just keeps the deterministic score (`llm_status: failed`).

**GitHub scoring.** One API call per unique user (`/users/{u}/repos`), bounded concurrency, de-duplicated in-flight
requests, 24 h disk cache, token read from `GITHUB_TOKEN`. 404/403/429/network errors are recorded as
`not_found`/`rate_limited`/`error`; after the first rate-limit the rest of the run short-circuits. Only *eligible*
candidates are enriched (saves quota). Missing GitHub = 0 of 10 points, never a rejection.
Unauthenticated limit is 60 req/h — **set `GITHUB_TOKEN` for a full run**.

## Output
`results.json` = `{summary, ranked_candidates[], rejected_candidates[], failed_files[], duplicate_files[]}`.
Each ranked entry: `rank, candidate_name, total_score, score_breakdown, penalties, matched_skills,
project_summary, evidence[], github{status,summary,…}, strengths, concerns, jd_similarity, llm_status`.

## Known limitations
- Regex/lexicon matching can miss unusual phrasing; the lexicon is intentionally easy to extend.
- Block segmentation is heuristic for exotic layouts (multi-column PDFs); a fallback treats the text as one block.
- Scanned (image-only) PDFs are reported as unreadable (no OCR).
- Recency uses repo `pushed_at`, not commit-level events.

## If I Had More Time
1. Calibrate weights against a hand-labelled set of ~20 resumes (precision@k) and move prototypes/lexicon to YAML.
2. OCR fallback (Tesseract) for scanned PDFs, plus layout-aware column detection.
3. GitHub: `/events` for commit-level recency, README/topic sniffing for AI relevance, ETag caching.
4. Evaluate LLM-vs-deterministic agreement; make `/screen` a background job with polling for large batches.
