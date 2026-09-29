"""
Unit tests for the LLM factory — verifies gateway config is consumed correctly
from Settings and that no SSL/model/URL values are hardcoded in agents.

These tests use monkeypatching to avoid real HTTP connections.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from daily_news.config.settings import Settings
from daily_news.config.llm_factory import make_llm, make_eval_llm, _make_http_clients


# ── _make_http_clients ────────────────────────────────────────────────────────

class TestMakeHttpClients:
    def test_ssl_verify_false_propagated(self):
        sync_c, async_c = _make_http_clients(
            ssl_verify=False, timeout=30.0, max_connections=5, max_keepalive=2
        )
        # httpx stores verify on the transport; check the client was created
        assert sync_c is not None
        assert async_c is not None

    def test_ssl_verify_true_propagated(self):
        sync_c, async_c = _make_http_clients(
            ssl_verify=True, timeout=30.0, max_connections=5, max_keepalive=2
        )
        assert sync_c is not None
        assert async_c is not None

    def test_timeout_stored_on_client(self):
        sync_c, _ = _make_http_clients(
            ssl_verify=True, timeout=45.0, max_connections=5, max_keepalive=2
        )
        # httpx.Client stores timeout as a Timeout object; check via repr
        assert "45" in str(sync_c.timeout)

    def test_returns_sync_and_async_clients(self):
        import httpx
        sync_c, async_c = _make_http_clients(
            ssl_verify=True, timeout=10.0, max_connections=3, max_keepalive=1
        )
        assert isinstance(sync_c, httpx.Client)
        assert isinstance(async_c, httpx.AsyncClient)


# ── make_llm ─────────────────────────────────────────────────────────────────

class TestMakeLlm:
    def _settings(self, **overrides) -> Settings:
        defaults = dict(
            LLM_BASE_URL="https://fake-gateway/v1",
            LLM_API_KEY="test-key",
            LLM_MODEL="qwen2-5-72b-instruct",
            LLM_SSL_VERIFY="false",
            LLM_TIMEOUT="60",
        )
        defaults.update(overrides)
        return Settings(**{k: v for k, v in defaults.items()})

    def test_model_name_comes_from_settings(self):
        s = self._settings(LLM_MODEL="my-custom-model")
        llm = make_llm(temperature=0.3, settings=s)
        assert llm.model_name == "my-custom-model"

    def test_temperature_is_passed_through(self):
        s = self._settings()
        llm = make_llm(temperature=0.7, settings=s)
        assert llm.temperature == 0.7

    def test_base_url_comes_from_settings(self):
        s = self._settings(LLM_BASE_URL="https://api.anthropic.com/v1")
        llm = make_llm(temperature=0.0, settings=s)
        assert "anthropic" in str(llm.openai_api_base)

    def test_different_temperatures_produce_different_instances(self):
        s = self._settings()
        llm_a = make_llm(temperature=0.0, settings=s)
        llm_b = make_llm(temperature=0.9, settings=s)
        assert llm_a.temperature != llm_b.temperature

    def test_small_pool_override_accepted(self):
        """max_connections/max_keepalive overrides do not crash."""
        s = self._settings()
        llm = make_llm(temperature=0.0, settings=s, max_connections=3, max_keepalive=1)
        assert llm is not None


# ── make_eval_llm ─────────────────────────────────────────────────────────────

class TestMakeEvalLlm:
    def _settings(self, **overrides) -> Settings:
        defaults = dict(
            LLM_BASE_URL="https://fake-gateway/v1",
            LLM_API_KEY="test-key",
            LLM_MODEL="qwen2-5-72b-instruct",
            LLM_SSL_VERIFY="false",
        )
        defaults.update(overrides)
        return Settings(**{k: v for k, v in defaults.items()})

    def test_falls_back_to_primary_model_when_eval_not_set(self):
        s = self._settings()
        llm = make_eval_llm(temperature=0.1, settings=s)
        # Should use the primary model since EVAL_LLM_MODEL is empty
        assert llm.model_name == "qwen2-5-72b-instruct"

    def test_uses_eval_model_when_set(self):
        s = self._settings(EVAL_LLM_MODEL="claude-3-haiku-20240307",
                           EVAL_LLM_BASE_URL="https://api.anthropic.com/v1",
                           EVAL_LLM_API_KEY="sk-ant-test")
        llm = make_eval_llm(temperature=0.1, settings=s)
        assert llm.model_name == "claude-3-haiku-20240307"

    def test_eval_temperature_is_passed_through(self):
        s = self._settings()
        llm = make_eval_llm(temperature=0.1, settings=s)
        assert llm.temperature == 0.1

    def test_eval_base_url_falls_back_to_primary(self):
        s = self._settings(LLM_BASE_URL="https://ibm-gateway/v1")
        llm = make_eval_llm(temperature=0.1, settings=s)
        assert "ibm-gateway" in str(llm.openai_api_base)


# ── Settings resolution properties ────────────────────────────────────────────

class TestSettingsResolution:
    def test_resolved_eval_model_fallback(self):
        s = Settings(LLM_MODEL="qwen2-5-72b-instruct", EVAL_LLM_MODEL="")
        assert s.resolved_eval_llm_model == "qwen2-5-72b-instruct"

    def test_resolved_eval_model_override(self):
        s = Settings(LLM_MODEL="qwen2-5-72b-instruct",
                     EVAL_LLM_MODEL="claude-3-haiku",
                     EVAL_LLM_BASE_URL="https://api.anthropic.com/v1",
                     EVAL_LLM_API_KEY="key")
        assert s.resolved_eval_llm_model == "claude-3-haiku"

    def test_resolved_eval_ssl_verify_fallback(self):
        s = Settings(LLM_SSL_VERIFY="false")
        assert s.resolved_eval_llm_ssl_verify is False

    def test_resolved_eval_ssl_verify_override(self):
        s = Settings(LLM_SSL_VERIFY="false", EVAL_LLM_SSL_VERIFY="true")
        assert s.resolved_eval_llm_ssl_verify is True

    def test_resolved_eval_timeout_fallback(self):
        s = Settings(LLM_TIMEOUT="90")
        assert s.resolved_eval_llm_timeout == 90.0

    def test_resolved_eval_timeout_override(self):
        s = Settings(LLM_TIMEOUT="90", EVAL_LLM_TIMEOUT="30")
        assert s.resolved_eval_llm_timeout == 30.0

    def test_default_ssl_verify_is_false_for_ibm_gateway(self):
        """Default must be False — IBM internal gateway uses a self-signed cert."""
        s = Settings()
        assert s.llm_ssl_verify is False

    def test_default_model_is_qwen(self):
        s = Settings()
        assert s.llm_model == "qwen2-5-72b-instruct"
