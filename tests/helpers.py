from pathlib import Path

from screener.config import Settings
from screener.eligibility import check_eligibility
from screener.features import scan
from screener.ingest import RawResume
from screener.parse import parse_resume
from screener.scoring import score_candidate
from screener.tfidf import SemanticScorer

S = Settings()


def analyse(text: str, name: str = "x.txt"):
    parsed = parse_resume(RawResume(Path(name), text=text))
    hits = scan(parsed)
    return parsed, hits, check_eligibility(parsed, hits, S)


def score(text: str, corpus=None):
    parsed, hits, el = analyse(text)
    sem = SemanticScorer(corpus or [b.text for b in parsed.blocks], S)
    return score_candidate(parsed, hits, el, sem, S), el


DEEP = """Asha Rao
asha@example.com | github.com/asha-rao
Skills
Python, FastAPI, PostgreSQL, Redis, Docker, GCP
Projects
Agentic RAG Support Assistant | Python, LangGraph, FastAPI, Qdrant
• Built a stateful multi-agent LangGraph workflow with tool calling and retrieval over a Qdrant vector store using embeddings and reranking.
• Added an evaluation pipeline measuring retrieval precision and hallucination rate; async FastAPI backend with Redis caching and retries.
• Deployed on GCP Cloud Run with Docker; wrote pytest tests and structured logging.
"""

THIN = """Bob Thin
bob@example.com
Skills
Python, Flask, OpenAI
Projects
ChatBuddy | Python, OpenAI API
• Built a chatbot using the OpenAI API that answers questions.
"""

JS_ONLY = """Carl Js
carl@example.com
Skills
JavaScript, React, Node.js, Java, Spring Boot
Projects
Shop | React, Node.js
• Built an e-commerce site with React and Node.js, used GitHub Copilot and AI-assisted development.
"""

PY_NO_AI = """Dina Py
dina@example.com
Skills
Python, Django, PostgreSQL
Projects
Inventory API | Django
• Built a REST API with Django and PostgreSQL serving 5,000 users.
"""
