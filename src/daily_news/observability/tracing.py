"""
Observability — OpenTelemetry + Langfuse v4.

Langfuse v4 API (SDK ≥ 4.0)
─────────────────────────────
v4 dropped the legacy `client.trace()` / `client.span()` imperative API.
The new API uses:

  • langfuse.langchain.CallbackHandler — drop-in LangChain/LangGraph callback
  • Langfuse.start_observation()       — returns a span object (.update() / .end())
  • Langfuse.create_trace_id(seed=)   — deterministic ID from a seed string
  • TraceContext                       — links separate call-sites to one trace

IMPORTANT: start_observation() returns a LangfuseSpan object — NOT a context
manager. Use span.update(output=...) then span.end() explicitly.

Refs
────
  SDK v4 overview : https://langfuse.com/docs/sdk/python
  LangChain       : https://langfuse.com/docs/integrations/langchain/tracing
  Changelog v4    : https://langfuse.com/changelog/2025-01-13-sdk-v3-stable

Usage
─────
Option A — LangChain CallbackHandler (LLM chains, agents):

    handler, trace_id = get_langfuse_callback(run_id="RUN-001", tags=["summary"])
    result = await chain.ainvoke({...}, config={"callbacks": [handler]})
    # v4 auto-flushes in background — no explicit flush needed per-call

Option B — Manual span (MCP calls, publishing, non-LLM steps):

    span = start_span("news.search", run_id=run_id, input={"query": q})
    result = await mcp_client.search_latest(q)
    if span:
        span.update(output={"count": len(result)})
        span.end()

Option C — @observe_node decorator (entire LangGraph nodes):

    @observe_node(name="summarize", as_type="chain")
    async def summarize(state: NewsWorkflowState) -> NewsWorkflowState:
        ...
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache, wraps
from typing import Any, Callable, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)


# ── Langfuse client singleton ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_langfuse_client():
    """
    Return a cached Langfuse client.  Returns None when keys are absent.
    lru_cache ensures the SDK is initialised exactly once per process.
    """
    from daily_news.config.settings import get_settings
    s = get_settings()
    if not s.langfuse_enabled:
        logger.info("Langfuse disabled — set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY to enable")
        return None
    try:
        from langfuse import Langfuse
        client = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host,
        )
        client.auth_check()  # raises if keys are invalid
        logger.info("Langfuse initialised and authenticated → %s", s.langfuse_host)
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse init/auth failed (LLM tracing disabled): %s", exc)
        return None


# ── LangChain / LangGraph callback handler ────────────────────────────────────

def get_langfuse_callback(
    run_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Return a (handler, trace_id) pair for a LangChain/LangGraph invocation.

    All LLM calls within one workflow run share trace_id = create_trace_id(seed=run_id)
    so they appear together in the Langfuse session view.

    Returns (None, None) when Langfuse is not configured.

    Ref: https://langfuse.com/docs/integrations/langchain/tracing
    """
    from daily_news.config.settings import get_settings
    s = get_settings()
    if not s.langfuse_enabled:
        return None, None
    try:
        from langfuse.langchain import CallbackHandler
        from langfuse.types import TraceContext

        client = get_langfuse_client()
        trace_id: str | None = None
        trace_context: TraceContext | None = None

        if client and run_id:
            trace_id = client.create_trace_id(seed=run_id)
            trace_context = TraceContext(trace_id=trace_id)

        handler = CallbackHandler(
            public_key=s.langfuse_public_key,
            trace_context=trace_context,
        )
        return handler, trace_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_langfuse_callback failed: %s", exc)
        return None, None


# ── Manual span (non-LangChain steps: MCP calls, publishing, etc.) ────────────

def start_span(
    name: str,
    run_id: str | None = None,
    as_type: str = "span",
    input: Any = None,  # noqa: A002
    metadata: dict[str, Any] | None = None,
):
    """
    Create a Langfuse span for non-LangChain steps.

    Returns a LangfuseSpan with .update() and .end() methods.
    Returns None when Langfuse is not configured.

        span = start_span("news.search_latest", run_id=run_id, input={"query": q})
        result = await mcp_client.search_latest(q)
        if span:
            span.update(output={"count": len(result)})
            span.end()
    """
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        from langfuse.types import TraceContext
        trace_context: TraceContext | None = None
        if run_id:
            trace_id = client.create_trace_id(seed=run_id)
            trace_context = TraceContext(trace_id=trace_id)

        return client.start_observation(
            name=name,
            as_type=as_type,
            trace_context=trace_context,
            input=input,
            metadata=metadata or {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("start_span(%s) failed: %s", name, exc)
        return None


# ── @observe_node decorator for LangGraph workflow nodes ─────────────────────

def observe_node(name: str | None = None, as_type: str = "agent"):
    """
    Decorator wrapping a LangGraph node with a Langfuse observation span.

    Auto-captures:
      - input  : run_id + workflow_status from state
      - output : new workflow_status + error count from return value

        @observe_node(name="summarize", as_type="chain")
        async def summarize(state: NewsWorkflowState) -> NewsWorkflowState:
            ...

    Falls back to plain function call when Langfuse is not configured.
    """
    def decorator(func: F) -> F:
        node_name = name or func.__name__

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            client = get_langfuse_client()
            if client is None:
                return await func(*args, **kwargs)

            state = args[0] if args else {}
            run_id = state.get("run_id") if isinstance(state, dict) else None

            span = start_span(
                name=node_name,
                run_id=run_id,
                as_type=as_type,
                input={"run_id": run_id, "workflow_status": state.get("workflow_status")},
                metadata={"node": node_name},
            )
            try:
                result = await func(*args, **kwargs)
                if span and isinstance(result, dict):
                    span.update(output={
                        "workflow_status": result.get("workflow_status"),
                        "errors":          len(result.get("errors", [])),
                    })
                return result
            except Exception as exc:
                if span:
                    span.update(
                        output={"error": str(exc)},
                        level="ERROR",
                        status_message=str(exc),
                    )
                raise
            finally:
                if span:
                    span.end()

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            client = get_langfuse_client()
            if client is None:
                return func(*args, **kwargs)
            state = args[0] if args else {}
            run_id = state.get("run_id") if isinstance(state, dict) else None
            span = start_span(name=node_name, run_id=run_id, as_type=as_type,
                               input={"run_id": run_id})
            try:
                return func(*args, **kwargs)
            finally:
                if span:
                    span.end()

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


# ── Flush ─────────────────────────────────────────────────────────────────────

def flush_langfuse() -> None:
    """
    Flush all pending Langfuse events.

    Call before process exit (CronJob end, test teardown) to ensure no
    buffered events are dropped.  v4 auto-flushes in the background but
    explicit flush guarantees delivery.
    """
    client = get_langfuse_client()
    if client:
        try:
            client.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Langfuse flush failed: %s", exc)


# ── Compatibility stub — used in daily_news_graph.py ─────────────────────────

def langfuse_trace(
    name: str,
    run_id: str | None = None,
    input: Any = None,  # noqa: A002
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
):
    """
    No-op stub — kept for call-site compatibility.
    In v4, use get_langfuse_callback / start_span / @observe_node instead.
    Returns None — all callers guard with `if trace:` so they become no-ops.
    """
    _ = (name, run_id, input, metadata, tags)
    return None


# ── OpenTelemetry setup ───────────────────────────────────────────────────────

def setup_tracing() -> None:
    """
    Initialise OTLP tracing and warm the Langfuse client singleton.

    Call once at process startup:
      - FastAPI app lifespan handler
      - workflow_runner.py before graph.ainvoke()
    """
    from daily_news.config.settings import get_settings
    s = get_settings()

    # ── OpenTelemetry ─────────────────────────────────────────────────────────
    if s.otel_exporter_otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            resource = Resource.create({"service.name": "daily-news-platform"})
            provider = TracerProvider(resource=resource)
            exporter = OTLPSpanExporter(endpoint=s.otel_exporter_otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            logger.info("OTLP tracing initialised → %s", s.otel_exporter_otlp_endpoint)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTLP tracing setup skipped: %s", exc)
    else:
        # No collector configured — install a no-op provider so the SDK never
        # attempts connections and never emits retry/connection-refused warnings.
        from opentelemetry.sdk.trace import TracerProvider as _TP
        _noop = _TP()   # provider with no exporters → all spans are dropped silently
        trace.set_tracer_provider(_noop)
        logger.info("OTLP tracing disabled — no endpoint configured")

    # ── Langfuse — warm the client singleton (auth check at startup) ──────────
    get_langfuse_client()


def get_tracer(name: str = "daily-news") -> trace.Tracer:
    return trace.get_tracer(name)
