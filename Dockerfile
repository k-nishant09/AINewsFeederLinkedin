# =============================================================================
# AI Daily News Platform — Main API Dockerfile (UBI9 Python 3.11)
# Uses OpenShift internal registry base image — no apt-get required
# =============================================================================
FROM image-registry.openshift-image-registry.svc:5000/openshift/python:3.11-ubi9

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PYTHONPATH=/app/src

WORKDIR /app

# ── Python deps ──────────────────────────────────────────────────────────────
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY prompts/ ./prompts/

# ── Separate pip install step so layer is cached when only src/ changes ──────
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install \
        fastapi "uvicorn[standard]" pydantic pydantic-settings \
        langchain langchain-core langchain-openai langgraph \
        "mcp[cli]" httpx tenacity \
        opentelemetry-api opentelemetry-sdk \
        opentelemetry-exporter-otlp-proto-http \
        opentelemetry-instrumentation-fastapi prometheus-client \
        langfuse python-dotenv structlog anyio

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "daily_news.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
