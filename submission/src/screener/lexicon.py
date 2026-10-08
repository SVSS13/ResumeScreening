"""Skill / evidence vocabulary. Pure data: edit here to change what counts as evidence."""
from __future__ import annotations

import re

I = re.I


def rx(p: str) -> re.Pattern:
    return re.compile(p, I)


# Phrases that mean "I USE AI tools" rather than "I BUILD AI systems" - stripped before matching.
AI_TOOL_NOISE = rx(
    r"\b(?:ai|llm|gen(?:erative)?[- ]?ai)[- ]assisted\s+\w+(?:\s+\w+)?|\bgithub copilot\b|\bcopilot\b|"
    r"\bai (?:coding |development )?tools?\b|\bvibe[- ]?coding\b|\bcursor ide\b"
)

# name -> (regex, tier).  (?-i:...) = case-sensitive island (avoids "rag", "adk" false hits).
SKILLS: dict[str, tuple[re.Pattern, str]] = {
    # ---- strong AI / agentic / RAG evidence (one is enough for eligibility) ----
    "LangChain": (rx(r"\blang[- ]?chain\b"), "strong"),
    "LangGraph": (rx(r"\blang[- ]?graph\b"), "strong"),
    "LlamaIndex": (rx(r"\bllama[- ]?index\b"), "strong"),
    "Google ADK": (rx(r"\bgoogle adk\b|\bagent development kit\b|(?-i:\bADK\b)"), "strong"),
    "CrewAI": (rx(r"\bcrew ?ai\b"), "strong"),
    "AutoGen": (rx(r"\bauto-?gen\b"), "strong"),
    "Semantic Kernel": (rx(r"\bsemantic kernel\b"), "strong"),
    "Haystack/DSPy": (rx(r"\bhaystack\b|\bdspy\b"), "strong"),
    "RAG": (rx(r"(?-i:\bRAG\b)|retrieval[- ]augmented"), "strong"),
    "Vector DB": (rx(r"\bvector (?:db|database|store|search|index)\w*|\bfaiss\b|\bchroma(?:db)?\b|\bpinecone\b|"
                    r"\bqdrant\b|\bweaviate\b|\bpgvector\b|\bmilvus\b"), "strong"),
    "Embeddings": (rx(r"\bembeddings?\b|\bsemantic search\b"), "strong"),
    "Tool Calling": (rx(r"\btool[- ]?(?:calling|use)\b|\bfunction[- ]calling\b"), "strong"),
    "MCP": (rx(r"(?-i:\bMCP\b)|model context protocol"), "strong"),
    "Agentic": (rx(r"\bagentic\b|\bmulti[- ]agents?\b|\b(?:ai|llm|autonomous|conversational|voice|research|"
                   r"coding) agents?\b|\breact agent\b"), "strong"),
    "LLM Evaluation": (rx(r"\bllm[- ]as[- ](?:a[- ])?judge\b|\bragas\b|\bllm eval\w*|\bevals\b"), "strong"),
    # ---- LLM usage (counts if inside a project/experience block) ----
    "LLM": (rx(r"\bllms?\b|\blarge language models?\b|\bgenerative ai\b|\bgenai\b|\bprompt engineering\b"), "llm"),
    "OpenAI": (rx(r"\bopenai\b|\bgpt[- ]?(?:3|4|5|4o)\w*|\bchatgpt\b"), "llm"),
    "Gemini": (rx(r"\bgemini\b"), "llm"),
    "Claude/Anthropic": (rx(r"\bclaude\b|\banthropic\b"), "llm"),
    "Open-source LLMs": (rx(r"\bollama\b|\bvllm\b|\bmistral\b|\bllama ?[23]\b|\bgroq\b"), "llm"),
    "HuggingFace": (rx(r"\bhugging ?face\b|\btransformers\b"), "llm"),
    "Chatbot": (rx(r"\bchat ?bots?\b|\bconversational ai\b"), "llm"),
    "NLP": (rx(r"\bnlp\b|natural language processing"), "llm"),
    # ---- classical ML / DL (NOT sufficient alone) ----
    "Scikit-learn": (rx(r"\bscikit-?learn\b|\bsklearn\b"), "ml"),
    "PyTorch": (rx(r"\bpytorch\b"), "ml"),
    "TensorFlow/Keras": (rx(r"\btensorflow\b|\bkeras\b"), "ml"),
    "ML/DL": (rx(r"\bmachine learning\b|\bdeep learning\b|\bneural networks?\b|\bcomputer vision\b|"
                 r"\bopencv\b|\byolo\w*\b|(?-i:\bCNN\b)|\bxgboost\b|\blightgbm\b"), "ml"),
    # ---- Python & Python-only ecosystem ----
    "Python": (rx(r"\bpython ?3?\b"), "python"),
    "FastAPI": (rx(r"\bfast ?api\b"), "pyfw"),
    "Flask": (rx(r"\bflask\b"), "pyfw"),
    "Django": (rx(r"\bdjango\b"), "pyfw"),
    "Pydantic": (rx(r"\bpydantic\b"), "pylib"),
    "Pandas/NumPy": (rx(r"\bpandas\b|\bnumpy\b"), "pylib"),
    "Streamlit/Gradio": (rx(r"\bstreamlit\b|\bgradio\b"), "pylib"),
    "Celery": (rx(r"\bcelery\b"), "pylib"),
    "Pytest": (rx(r"\bpytest\b"), "pylib"),
    "SQLAlchemy": (rx(r"\bsqlalchemy\b"), "pylib"),
    # ---- databases / queues ----
    "PostgreSQL": (rx(r"\bpostgres(?:ql)?\b|\bpsql\b|\bsupabase\b"), "db_pg"),
    "Redis": (rx(r"\bredis\b"), "db_redis"),
    "MySQL/SQLite/SQL": (rx(r"\bmysql\b|\bsqlite\b|\bsql server\b|(?-i:\bSQL\b)|\bmongodb\b|\bdynamodb\b"), "db_other"),
    "Kafka/RabbitMQ": (rx(r"\bkafka\b|\brabbitmq\b|\bsqs\b|\bpub/?sub\b"), "queue"),
    # ---- cloud / devops ----
    "GCP": (rx(r"\bgcp\b|\bgoogle cloud\b|\bcloud run\b|\bbigquery\b|\bvertex ai\b|\bgke\b|\bfirebase\b"), "cloud_gcp"),
    "AWS": (rx(r"\baws\b|\bamazon web services\b|\bec2\b|\blambda\b|\bs3\b|\bsagemaker\b|\bbedrock\b"), "cloud_other"),
    "Azure": (rx(r"\bazure\b"), "cloud_other"),
    "Docker": (rx(r"\bdocker\b|\bcontainer(?:s|ized|ised)?\b"), "docker"),
    "Kubernetes": (rx(r"\bkubernetes\b|\bk8s\b|\bhelm\b"), "k8s"),
    "CI/CD": (rx(r"\bci/?cd\b|\bgithub actions\b|\bjenkins\b|\bgitlab ci\b"), "cicd"),
    # ---- other languages / frontend ----
    "JavaScript/TypeScript": (rx(r"\bjavascript\b|\btypescript\b"), "lang"),
    "Java": (rx(r"\bjava\b(?!script)"), "lang"),
    "C/C++": (rx(r"\bc\+\+\b"), "lang"),
    "React": (rx(r"\breact(?:\.?js)?\b"), "frontend"),
    "Next.js": (rx(r"\bnext\.?js\b"), "frontend"),
    "Node.js": (rx(r"\bnode(?:\.?js)?\b|\bexpress(?:\.?js)?\b|\bnestjs\b"), "frontend"),
    "Spring Boot": (rx(r"\bspring ?boot\b"), "lang"),
}

PYTHON_IMPLIED_TIERS = {"pyfw", "pylib"}

# Depth dimensions of an AI project. Evaluated per block, only for blocks that already contain AI terms.
DEPTH_DIMS: dict[str, re.Pattern] = {
    "retrieval": rx(r"retriev|vector|embedding|faiss|chroma|pinecone|qdrant|weaviate|pgvector|semantic search|"
                    r"rerank|chunk|bm25|hybrid search|knowledge base"),
    "orchestration_state": rx(r"langgraph|state ?machine|stateful|orchestrat|multi[- ]agent|checkpoint|agent memory|conversation memory|"
                              r"planner|supervisor|react agent|crew|workflow engine|agent(?:ic)? workflows?|conditional routing|"
                              r"routing|human[- ]in[- ]the[- ]loop"),
    "tools": rx(r"tool[- ]?(?:calling|use)|function[- ]calling|\bmcp\b|model context protocol|"
                r"(?:agent|api|external|custom|function) tools?\b"),
    "evaluation": rx(r"\bevals?\b|evaluat|benchmark|ragas|precision|recall|\bf1\b|accuracy|hallucinat|guardrail|"
                     r"llm[- ]as|test set"),
    "data_processing": rx(r"\bpars(?:e|ing|er)\b|ingest|\bocr\b|extract|\bpipeline|\betl\b|preprocess|schema|"
                          r"pydantic|structured output|json|classif|dedup|normaliz"),
    "production": rx(r"deployed|production|latency|throughput|real-?time|async|queue|cache|caching|streaming|"
                     r"\bsse\b|docker|kubernetes|cloud run|rate[- ]limit|concurren"),
}
STRUCTURAL_DIMS = ["retrieval", "orchestration_state", "tools", "evaluation", "data_processing"]

THIN_CUES = rx(r"\bchat ?bots?\b|\bchatgpt\b|\bopenai api\b|\bgemini api\b|\bgpt[- ]?[345]\b|\bai[- ]powered\b|"
               r"\bwrapper\b|\bprompt")
TUTORIAL_CUES = rx(r"\btutorial\b|\budemy\b|\bcoursera\b|\bfollowing (?:a|the)\b|\bcourse project\b|"
                   r"\bclone of\b|\byoutube\b|\bbootcamp\b|\breplica of\b")
OWNERSHIP_VERBS = rx(r"\b(?:built|designed|implemented|architected|engineered|developed|created|led|owned|"
                     r"deployed|optimi[sz]ed|integrated|established|introduced)\b")

# Non-trivial engineering signals (1 point per distinct category, cap 5)
ENG_DEPTH: dict[str, re.Pattern] = {
    "testing": rx(r"\bpytest\b|unit test|integration test|test coverage|\btdd\b|\btests?\b|\bqa\b"),
    "architecture": rx(r"microservice|event[- ]driven|clean architecture|design pattern|system design|"
                       r"modular|layered|domain[- ]driven|\bcqrs\b|architect"),
    "caching": rx(r"\bcach(?:e|ing|ed)\b|\blru\b|memoiz"),
    "queues": rx(r"\bkafka\b|rabbitmq|\bcelery\b|\bsqs\b|pub/?sub|message queue|job queue|background (?:job|task|worker)s?"),
    "observability": rx(r"observab|monitoring|prometheus|grafana|sentry|tracing|opentelemetry|cloudwatch|logging|telemetry"),
    "concurrency": rx(r"\basync\w*|concurren|\bthread|parallel|multiprocess|\bworkers?\b"),
    "failure_handling": rx(r"\bretr(?:y|ies)\b|fallback|circuit[- ]breaker|idempoten|rate[- ]limit|graceful|"
                           r"dead[- ]letter|timeout|backoff|fault[- ]toleran"),
}

BACKEND_PRACTICE = rx(r"\brest(?:ful)?\b|microservice|websocket|\bjwt\b|oauth|\bgraphql\b|\bgrpc\b|\bsse\b")
ASYNC_RX = rx(r"\basync\w*|\bawait\b|\bconcurren\w+|\bcelery\b|\bbackground (?:job|task|worker)s?\b")
DEPLOY_RX = rx(r"\bdeploy\w*|\bhosted\b|\bvercel\b|\brender\b|\bheroku\b|\bcloud run\b|\blive (?:link|demo)\b")
