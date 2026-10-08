# Multi-stage, non-root slim Dockerfile for Resume Screener backend
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


FROM python:3.12-slim AS runner

WORKDIR /app

# Create unprivileged non-root user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

# Copy installed packages from builder
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONPATH=/app/src

# Copy application source code
COPY --chown=appuser:appgroup src/ /app/src/
COPY --chown=appuser:appgroup api.py main.py /app/
COPY --chown=appuser:appgroup .env.example /app/.env.example

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Note on Uvicorn Workers:
# In-memory caches and token bucket limiters are per-process. Running with --workers > 1
# gives each worker an independent cache and bucket (multi-worker shared memory is explicitly out of scope).
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
