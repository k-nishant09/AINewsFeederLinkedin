# =============================================================================
# AI Daily News Platform — Main API Dockerfile (UBI9 Python 3.11)
# Uses OpenShift internal registry base image — no apt-get required
# =============================================================================
# Local Podman / CI — use public slim image
# OpenShift deployment uses: image-registry.openshift-image-registry.svc:5000/openshift/python:3.11-ubi9
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PYTHONPATH=/app/src

WORKDIR /app

# ── System deps for cairosvg (SVG → PNG for comic strip rendering) ────────────
# python:3.11-slim is Debian bookworm; cairo packages have the correct names below.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libcairo2 libcairo2-dev \
        libpango-1.0-0 libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev pkg-config \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

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
        langfuse python-dotenv structlog anyio \
        cairosvg

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "daily_news.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
