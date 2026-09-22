# evaluation_mcp MCP Server — UBI9 Python 3.11
FROM image-registry.openshift-image-registry.svc:5000/openshift/python:3.11-ubi9

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app

WORKDIR /app

COPY mcp_servers/__init__.py ./mcp_servers/__init__.py
COPY mcp_servers/evaluation_mcp/ ./mcp_servers/evaluation_mcp/

RUN pip install --upgrade pip && \
    pip install \
        fastapi "uvicorn[standard]" pydantic pydantic-settings \
        "mcp[cli]" httpx tenacity \
        langfuse python-dotenv anyio

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["python", "-m", "mcp_servers.evaluation_mcp.server"]
