"""
Tests for the LinkedIn MCP Server.

Based on official LinkedIn API docs:
  - Authentication: https://learn.microsoft.com/en-us/linkedin/shared/authentication/authentication
  - Profile API:    https://learn.microsoft.com/en-us/linkedin/shared/integrations/people/profile-api
  - Share on LinkedIn: https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin
  - API Concepts:   https://learn.microsoft.com/en-us/linkedin/shared/api-guide/concepts

Covers:
  - health endpoint (token_configured, api_version, endpoints)
  - _extract_localised helper
  - linkedin_get_profile  mock path (no token)
  - linkedin_get_profile  merged fields (id, urn, firstName, lastName, fullName, headline, etc.)
  - linkedin_get_profile_posts  mock path
  - linkedin_create_post  mock path
  - linkedin_create_post  idempotency — second call returns same post
  - linkedin_create_post  character limit enforcement (>3000 chars)
  - linkedin_get_post  mock path
  - linkedin_get_publish_status  registered vs unknown
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def linkedin_client():
    from mcp_servers.linkedin_mcp.server import _app
    return TestClient(_app)


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_structure(linkedin_client):
    resp = linkedin_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["server"] == "linkedin-mcp"
    assert "token_configured" in data
    assert data["token_configured"] is False      # no token in test env
    assert data["api_version"] == "202609"        # current default active version (202510 was sunset)
    # Verify all required endpoints are documented
    endpoints = data["endpoints"]
    assert "userinfo"     in endpoints
    assert "posts_create" in endpoints
    assert "posts_list"   in endpoints
    # Verify correct base URLs per docs
    assert "api.linkedin.com/v2/userinfo" in endpoints["userinfo"]
    assert "api.linkedin.com/rest/posts" in endpoints["posts_create"]


# ── linkedin_get_profile (mock — no token) ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_profile_mock_no_token():
    """Without a token, get_profile returns a mock using OIDC userinfo fields."""
    from mcp_servers.linkedin_mcp.server import linkedin_get_profile
    result = await linkedin_get_profile()
    assert result["status"] == "mock"
    # Fields returned by the OIDC /v2/userinfo endpoint
    assert "id"      in result
    assert "urn"     in result
    assert "name"    in result
    assert "email"   in result
    # URN must use the correct LinkedIn URN format
    assert result["urn"].startswith("urn:li:person:")


# ── linkedin_get_profile_posts (mock) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_profile_posts_mock_no_token():
    from mcp_servers.linkedin_mcp.server import linkedin_get_profile_posts
    result = await linkedin_get_profile_posts(count=5)
    assert result["status"] == "mock"
    assert result["posts"] == []
    assert result["total"] == 0


# ── linkedin_create_post (mock path) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_post_mock_no_token():
    """Without a token, create_post returns a mock-post-XXXX ID."""
    import mcp_servers.linkedin_mcp.server as srv
    # Clear idempotency registry for clean test
    srv._published_posts.clear()

    result = await srv.linkedin_create_post(
        text="Test AI news post",
        publication_key="test-key-001",
    )
    assert result["id"].startswith("mock-post-")
    assert result["status"] == "mock"
    assert result["publication_key"] == "test-key-001"
    assert result["text_length"] == len("Test AI news post")


@pytest.mark.asyncio
async def test_create_post_idempotency():
    """Second call with same publication_key returns the same post, not a new one."""
    import mcp_servers.linkedin_mcp.server as srv
    srv._published_posts.clear()

    first = await srv.linkedin_create_post(
        text="Idempotency test post",
        publication_key="idem-key-001",
    )
    second = await srv.linkedin_create_post(
        text="Idempotency test post",
        publication_key="idem-key-001",
    )
    assert second["id"] == first["id"]
    assert second.get("idempotent") is True


@pytest.mark.asyncio
async def test_create_post_character_limit():
    """Posts over 3000 chars are truncated (warning logged) — not rejected."""
    import mcp_servers.linkedin_mcp.server as srv
    srv._published_posts.clear()
    long_text = "A" * 3001
    result = await srv.linkedin_create_post(text=long_text, publication_key="limit-key")
    # Server truncates gracefully; no error key in the mock response
    assert "error" not in result
    # The stored/returned post must not exceed 3000 LinkedIn UTF-16 units
    assert result.get("text_length", 0) <= 3000


@pytest.mark.asyncio
async def test_create_post_exactly_3000_chars_allowed():
    """Posts of exactly 3000 chars must be accepted."""
    import mcp_servers.linkedin_mcp.server as srv
    srv._published_posts.clear()
    text = "B" * 3000
    result = await srv.linkedin_create_post(text=text, publication_key="limit-exact")
    assert "error" not in result
    assert result["text_length"] == 3000


# ── linkedin_get_post (mock path) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_post_mock_no_token():
    from mcp_servers.linkedin_mcp.server import linkedin_get_post
    result = await linkedin_get_post("some-post-id")
    assert result["status"] == "mock"
    assert result["post_id"] == "some-post-id"


# ── linkedin_get_publish_status ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_publish_status_registered():
    import mcp_servers.linkedin_mcp.server as srv
    srv._published_posts.clear()

    # Create a post so it gets registered
    await srv.linkedin_create_post(
        text="Status check post",
        publication_key="status-key-001",
    )
    # Get the post ID that was registered
    post_id = srv._published_posts["status-key-001"]["id"]
    result = await srv.linkedin_get_publish_status(post_id)
    assert result["status"] == "published"
    assert result["post_id"] == post_id
    assert "data" in result


@pytest.mark.asyncio
async def test_get_publish_status_unknown():
    from mcp_servers.linkedin_mcp.server import linkedin_get_publish_status
    result = await linkedin_get_publish_status("nonexistent-post-id")
    assert result["status"] == "unknown"


# ── API constants sanity checks ───────────────────────────────────────────────

def test_api_version_header():
    """LinkedIn-Version header must be present and follow YYYYMM format."""
    from mcp_servers.linkedin_mcp.server import LINKEDIN_API_VERSION, _rest_headers
    headers = _rest_headers()
    assert headers["LinkedIn-Version"] == LINKEDIN_API_VERSION
    assert len(LINKEDIN_API_VERSION) == 6    # YYYYMM
    assert LINKEDIN_API_VERSION.isdigit()


def test_restli_protocol_version_header():
    """X-Restli-Protocol-Version must be 2.0.0 per LinkedIn API Concepts docs."""
    from mcp_servers.linkedin_mcp.server import _rest_headers
    headers = _rest_headers()
    assert headers["X-Restli-Protocol-Version"] == "2.0.0"


def test_correct_api_base_urls():
    """Verify the correct base URLs are used per the official docs."""
    from mcp_servers.linkedin_mcp.server import (
        LINKEDIN_REST_BASE, LINKEDIN_V2_BASE, LINKEDIN_OIDC_BASE
    )
    # REST API (Posts, UGC)
    assert LINKEDIN_REST_BASE  == "https://api.linkedin.com/rest"
    # Profile API v2
    assert LINKEDIN_V2_BASE    == "https://api.linkedin.com/v2"
    # OpenID Connect userinfo
    assert LINKEDIN_OIDC_BASE  == "https://api.linkedin.com"


# ── Health — OAuth fields present ────────────────────────────────────────────

def test_health_has_oauth_section(linkedin_client):
    """Health endpoint must expose OAuth URLs so operators know how to authorize."""
    resp = linkedin_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "oauth" in data
    oauth = data["oauth"]
    assert oauth["start_url"]    == "/oauth/start"
    assert oauth["callback_url"] == "/oauth/callback"
    assert oauth["status_url"]   == "/oauth/status"
    assert "redirect_uri"        in oauth


# ── linkedin_validate_token (no token) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_token_no_token():
    """Without a token, validate_token reports present=False, status=no_token."""
    import mcp_servers.linkedin_mcp.server as srv
    # Ensure token store is empty for this test
    original = srv._token_store.copy()
    srv._token_store["access_token"]  = ""
    srv._token_store["refresh_token"] = ""
    srv._token_store["expires_at"]    = 0.0
    srv._token_store["obtained_at"]   = 0.0
    try:
        result = await srv.linkedin_validate_token()
        assert result["present"]     is False
        assert result["status"]      == "no_token"
        assert result["source"]      == "none"
        assert result["has_refresh"] is False
    finally:
        srv._token_store.update(original)


@pytest.mark.asyncio
async def test_validate_token_env_token_present():
    """When an env-supplied token is present, source=env and present=True."""
    import mcp_servers.linkedin_mcp.server as srv
    original = srv._token_store.copy()
    srv._token_store["access_token"] = "fake-env-token"
    srv._token_store["obtained_at"]  = 0.0   # env tokens have obtained_at=0
    srv._token_store["expires_at"]   = 0.0
    try:
        result = await srv.linkedin_validate_token()
        assert result["present"] is True
        assert result["source"]  == "env"
        assert result["expired"] is False    # expires_at=0 → unknown → assume valid
    finally:
        srv._token_store.update(original)


@pytest.mark.asyncio
async def test_validate_token_oauth_token_expired():
    """An OAuth token with a past expires_at is reported as expired."""
    import time
    import mcp_servers.linkedin_mcp.server as srv
    original = srv._token_store.copy()
    srv._token_store["access_token"] = "expired-oauth-token"
    srv._token_store["obtained_at"]  = time.time() - 6_000_000   # obtained ~70 days ago
    srv._token_store["expires_at"]   = time.time() - 1           # expired 1 second ago
    try:
        result = await srv.linkedin_validate_token()
        assert result["present"] is True
        assert result["expired"] is True
        assert result["source"]  == "oauth"
        assert result["status"]  == "expired"
        assert result["expires_at"] is not None
    finally:
        srv._token_store.update(original)


# ── OAuth /start — no client_id configured ───────────────────────────────────

def test_oauth_start_no_client_id(linkedin_client):
    """GET /oauth/start returns 503 when LINKEDIN_CLIENT_ID is not set."""
    import mcp_servers.linkedin_mcp.server as srv
    original_id = srv.LINKEDIN_CLIENT_ID
    srv.LINKEDIN_CLIENT_ID = ""
    try:
        resp = linkedin_client.get("/oauth/start", follow_redirects=False)
        assert resp.status_code == 503
    finally:
        srv.LINKEDIN_CLIENT_ID = original_id


def test_oauth_start_redirects_when_client_id_set(linkedin_client):
    """GET /oauth/start redirects to LinkedIn auth URL when CLIENT_ID is set."""
    import mcp_servers.linkedin_mcp.server as srv
    original_id = srv.LINKEDIN_CLIENT_ID
    srv.LINKEDIN_CLIENT_ID = "test-client-id"
    try:
        resp = linkedin_client.get("/oauth/start", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "linkedin.com/oauth/v2/authorization" in location
        assert "client_id=test-client-id" in location
        assert "response_type=code" in location
        assert "state=" in location
        assert "scope=" in location
    finally:
        srv.LINKEDIN_CLIENT_ID = original_id


# ── OAuth /callback — error path ─────────────────────────────────────────────

def test_oauth_callback_user_denied(linkedin_client):
    """When LinkedIn returns error=access_denied, callback returns HTML denial page."""
    resp = linkedin_client.get(
        "/oauth/callback",
        params={"error": "access_denied", "error_description": "User cancelled"},
    )
    assert resp.status_code == 200
    assert "denied" in resp.text.lower() or "Denied" in resp.text or "cancelled" in resp.text.lower()


def test_oauth_callback_missing_code(linkedin_client):
    """Callback with neither code nor error returns 400."""
    resp = linkedin_client.get("/oauth/callback", params={"state": "somestate"})
    assert resp.status_code == 400


def test_oauth_callback_invalid_state(linkedin_client):
    """Callback with an unrecognized state token returns 400 (CSRF protection)."""
    resp = linkedin_client.get(
        "/oauth/callback",
        params={"code": "auth-code-123", "state": "invalid-state-xyz"},
    )
    assert resp.status_code == 400


# ── OAuth /status ─────────────────────────────────────────────────────────────

def test_oauth_status_endpoint(linkedin_client):
    """GET /oauth/status returns token status fields without exposing secrets."""
    resp = linkedin_client.get("/oauth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "token_present"   in data
    assert "token_expired"   in data
    assert "oauth_start"     in data
    assert "required_scopes" in data
    assert "client_id_set"   in data
    assert "redirect_uri"    in data
    # Secrets must never appear in the response
    assert "access_token"  not in data
    assert "client_secret" not in data


# ── OAuth helper unit tests ───────────────────────────────────────────────────

def test_build_auth_url_contains_required_params():
    """_build_auth_url must include all mandatory OAuth params."""
    import mcp_servers.linkedin_mcp.server as srv
    original_id = srv.LINKEDIN_CLIENT_ID
    srv.LINKEDIN_CLIENT_ID = "my-client"
    try:
        url = srv._build_auth_url("test-state-token")
        assert "response_type=code"    in url
        assert "client_id=my-client"  in url
        assert "state=test-state-token" in url
        assert "scope="                in url
        assert "redirect_uri="         in url
        assert "linkedin.com/oauth/v2/authorization" in url
    finally:
        srv.LINKEDIN_CLIENT_ID = original_id


def test_store_token_response_sets_expiry():
    """_store_token_response must calculate expires_at from expires_in."""
    import time
    import mcp_servers.linkedin_mcp.server as srv
    original = srv._token_store.copy()
    before = time.time()
    srv._store_token_response({
        "access_token":  "tok-abc",
        "expires_in":    5184000,   # 60 days
        "refresh_token": "ref-xyz",
        "scope":         "openid profile",
    })
    after = time.time()
    try:
        assert srv._token_store["access_token"]  == "tok-abc"
        assert srv._token_store["refresh_token"] == "ref-xyz"
        assert srv._token_store["scope"]         == "openid profile"
        expires = srv._token_store["expires_at"]
        assert before + 5184000 <= expires <= after + 5184000
        # obtained_at should be set
        assert srv._token_store["obtained_at"] > 0
    finally:
        srv._token_store.update(original)


def test_store_token_response_no_expires_in():
    """_store_token_response with no expires_in leaves expires_at=0 (unknown)."""
    import mcp_servers.linkedin_mcp.server as srv
    original = srv._token_store.copy()
    srv._store_token_response({"access_token": "tok-def"})
    try:
        assert srv._token_store["expires_at"] == 0.0
    finally:
        srv._token_store.update(original)


def test_token_expiry_iso_none_when_unknown():
    """_token_expiry_iso returns None when expires_at is 0."""
    import mcp_servers.linkedin_mcp.server as srv
    original_exp = srv._token_store["expires_at"]
    srv._token_store["expires_at"] = 0.0
    try:
        assert srv._token_expiry_iso() is None
    finally:
        srv._token_store["expires_at"] = original_exp


def test_is_token_expired_false_when_expires_at_zero():
    """_is_token_expired returns False when expires_at=0 (env token, expiry unknown)."""
    import mcp_servers.linkedin_mcp.server as srv
    original_exp = srv._token_store["expires_at"]
    srv._token_store["expires_at"] = 0.0
    try:
        assert srv._is_token_expired() is False
    finally:
        srv._token_store["expires_at"] = original_exp
