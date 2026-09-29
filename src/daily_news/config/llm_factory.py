"""
LLM Factory — the single place that constructs every ChatOpenAI in the pipeline.

CURRENT SETUP
─────────────
IBM OpenShift Model Gateway (OpenAI-compatible)
  LLM_BASE_URL=https://model-gateway.apps.your-cluster.example.com/v1
  LLM_API_KEY=<bearer-token>
  LLM_MODEL=qwen2-5-72b-instruct
  LLM_SSL_VERIFY=false   ← IBM internal gateway uses a self-signed cert

TO SWITCH GATEWAY IN THE FUTURE (zero code changes required)
─────────────────────────────────────────────────────────────
Anthropic (Claude):
  LLM_BASE_URL=https://api.anthropic.com/v1
  LLM_API_KEY=sk-ant-...
  LLM_MODEL=claude-3-5-sonnet-20241022
  LLM_SSL_VERIFY=true        ← public endpoints have valid certs
  LLM_TIMEOUT=60

OpenAI:
  LLM_BASE_URL=https://api.openai.com/v1
  LLM_API_KEY=sk-...
  LLM_MODEL=gpt-4o
  LLM_SSL_VERIFY=true

Local Ollama (dev):
  LLM_BASE_URL=http://localhost:11434/v1
  LLM_API_KEY=ollama
  LLM_MODEL=llama3.1:8b
  LLM_SSL_VERIFY=true

For an independent judge model (different from the generator):
  EVAL_LLM_BASE_URL=https://api.anthropic.com/v1
  EVAL_LLM_API_KEY=sk-ant-...
  EVAL_LLM_MODEL=claude-3-haiku-20240307
  EVAL_LLM_SSL_VERIFY=true

All agent logic, prompts, and graph topology remain unchanged.
"""
from __future__ import annotations

import httpx
from langchain_openai import ChatOpenAI

from daily_news.config.settings import Settings, get_settings


def _make_http_clients(
    ssl_verify: bool,
    timeout: float,
    max_connections: int,
    max_keepalive: int,
    keepalive_expiry: float = 60.0,
) -> tuple[httpx.Client, httpx.AsyncClient]:
    """Return a matched sync + async httpx client pair.

    ssl_verify=False is only needed for IBM-internal gateways that serve
    self-signed TLS certificates.  Any public provider (Anthropic, OpenAI,
    Azure, Groq, Together AI) should use ssl_verify=True.
    """
    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive,
        keepalive_expiry=keepalive_expiry,
    )
    return (
        httpx.Client(verify=ssl_verify, limits=limits, timeout=timeout),
        httpx.AsyncClient(verify=ssl_verify, limits=limits, timeout=timeout),
    )


def make_llm(
    temperature: float,
    *,
    settings: Settings | None = None,
    max_connections: int | None = None,
    max_keepalive: int | None = None,
    keepalive_expiry: float = 60.0,
) -> ChatOpenAI:
    """Build a ChatOpenAI for the primary LLM gateway.

    ``temperature`` is the only caller-supplied argument — it is the one value
    that intentionally differs between agents (creative vs deterministic).
    Everything else — model, URL, API key, SSL, timeouts — comes from Settings.
    """
    s = settings or get_settings()
    sync_client, async_client = _make_http_clients(
        ssl_verify=s.llm_ssl_verify,
        timeout=s.llm_timeout,
        max_connections=max_connections or s.llm_max_connections,
        max_keepalive=max_keepalive or s.llm_max_keepalive,
        keepalive_expiry=keepalive_expiry,
    )
    return ChatOpenAI(
        model=s.llm_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        temperature=temperature,
        http_client=sync_client,
        http_async_client=async_client,
    )


def make_eval_llm(
    temperature: float,
    *,
    settings: Settings | None = None,
    max_connections: int = 10,
    max_keepalive: int = 4,
    keepalive_expiry: float = 60.0,
) -> ChatOpenAI:
    """Build a ChatOpenAI for the evaluator / judge gateway.

    Falls back to the primary gateway when EVAL_LLM_* env vars are not set,
    so a single-gateway setup works with no extra configuration.
    """
    s = settings or get_settings()
    sync_client, async_client = _make_http_clients(
        ssl_verify=s.resolved_eval_llm_ssl_verify,
        timeout=s.resolved_eval_llm_timeout,
        max_connections=max_connections,
        max_keepalive=max_keepalive,
        keepalive_expiry=keepalive_expiry,
    )
    return ChatOpenAI(
        model=s.resolved_eval_llm_model,
        api_key=s.resolved_eval_llm_api_key,
        base_url=s.resolved_eval_llm_base_url,
        temperature=temperature,
        http_client=sync_client,
        http_async_client=async_client,
    )
