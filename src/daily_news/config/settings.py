"""Application settings — loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    run_id_prefix: str = "RUN"

    # ── LLM Gateway (IBM OpenShift Model Gateway — OpenAI-compatible) ─────────
    # Default to IBM gateway; override via LLM_API_KEY env var in production.
    llm_api_key: str = Field("", alias="LLM_API_KEY")
    llm_base_url: str = Field(
        "",
        alias="LLM_BASE_URL",
    )
    llm_model: str = Field("qwen2-5-72b-instruct", alias="LLM_MODEL")

    # ── Jev System One Gateway ────────────────────────────────────────────────
    # System One model for fast structured decisions: article scoring,
    # persona routing, and content evaluation.
    # Ref: https://typesafe.ai/blog/introducing-system-one-models-and-jev
    #
    # Endpoint:  POST /v1/systemone
    # Health:    GET  /health  (no auth required)
    # Model:     Qwen/Qwen3.5-2B  method: lora_decision_head
    jev_base_url: str = Field(
        "",
        alias="JEV_BASE_URL",
    )
    jev_api_key: str = Field("", alias="JEV_API_KEY")
    # Set JEV_ENABLED=false to fall back to LLM-based evaluation / blind article selection.
    jev_enabled: bool = Field(True, alias="JEV_ENABLED")

    # ── MCP Server URLs ───────────────────────────────────────────────────────
    # Defaults are localhost ports used by docker-compose / local dev.
    # Override via env vars in OpenShift (injected from ConfigMap).
    news_mcp_url: str = Field("http://localhost:8101/mcp", alias="NEWS_MCP_URL")
    pageindex_mcp_url: str = Field("http://localhost:8102/mcp", alias="PAGEINDEX_MCP_URL")
    evaluation_mcp_url: str = Field("http://localhost:8103/mcp", alias="EVALUATION_MCP_URL")
    linkedin_mcp_url: str = Field("http://localhost:8104/mcp", alias="LINKEDIN_MCP_URL")

    # ── MCP Auth ──────────────────────────────────────────────────────────────
    mcp_auth_token: str = Field("", alias="MCP_AUTH_TOKEN")

    # ── External APIs ─────────────────────────────────────────────────────────
    # GNews — sole news provider
    # Docs: https://docs.gnews.io
    # Key: 32-char hex from https://gnews.io/dashboard
    gnews_api_key: str = Field("", alias="GNEWS_API_KEY")
    gnews_max_per_request: int = Field(10, alias="GNEWS_MAX_PER_REQUEST")
    gnews_request_delay_ms: int = Field(1100, alias="GNEWS_REQUEST_DELAY_MS")

    linkedin_access_token: str = Field("", alias="LINKEDIN_ACCESS_TOKEN")
    linkedin_client_id: str = Field("", alias="LINKEDIN_CLIENT_ID")
    linkedin_client_secret: str = Field("", alias="LINKEDIN_CLIENT_SECRET")

    # ── Publishing gate ───────────────────────────────────────────────────────
    # Set PUBLISHING_ENABLED=false during deployment smoke tests to prevent
    # accidental posts. Defaults true in production (ConfigMap injects it).
    publishing_enabled: bool = Field(True, alias="PUBLISHING_ENABLED")

    # ── Observability ─────────────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = Field("", alias="OTEL_EXPORTER_OTLP_ENDPOINT")

    # Langfuse — LLM/agent tracing
    # Docs: https://langfuse.com/docs/get-started
    # US cloud: https://us.cloud.langfuse.com
    # EU cloud: https://cloud.langfuse.com
    langfuse_public_key: str = Field("", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field("", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field("https://us.cloud.langfuse.com", alias="LANGFUSE_BASE_URL")

    @property
    def langfuse_enabled(self) -> bool:
        """True when both Langfuse keys are present."""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    # ── Evaluation thresholds ─────────────────────────────────────────────────
    # Calibrated for Jev score-based outputs (scores normalised 0-1 from 0..4).
    # Jev factuality/groundedness scores ~0.5-0.7 on good content; hallucination
    # ~0.2-0.4 on clean content.  These match the ConfigMap values in the cluster.
    eval_factuality_threshold: float = Field(0.50, alias="EVAL_FACTUALITY_THRESHOLD")
    eval_groundedness_threshold: float = Field(0.50, alias="EVAL_GROUNDEDNESS_THRESHOLD")
    eval_hallucination_threshold: float = Field(0.85, alias="EVAL_HALLUCINATION_THRESHOLD")
    eval_max_retries: int = 2


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
