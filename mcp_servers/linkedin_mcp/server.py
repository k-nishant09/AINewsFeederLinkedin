"""
LinkedIn MCP Server — production implementation based on official LinkedIn API docs.

API References
──────────────
OAuth 2.0 3-Legged Authorization Code Flow:
  https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow
  Authorization URL : https://www.linkedin.com/oauth/v2/authorization
  Token URL        : https://www.linkedin.com/oauth/v2/accessToken
  Scopes           : openid profile email w_member_social
  Token lifetime   : 60 days (access), 365 days (refresh — if refresh tokens enabled)
  Refresh tokens   : https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens

Official Python client (reference for OAuth flow):
  https://github.com/linkedin-developers/linkedin-api-python-client

Posts API (REST, replaces ugcPosts):
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
  POST https://api.linkedin.com/rest/posts
  Requires scope: w_member_social
  Success: HTTP 201, post URN in x-restli-id response header

Comments API (REST):
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api
  POST https://api.linkedin.com/rest/socialActions/{postUrn}/comments
  GET  https://api.linkedin.com/rest/socialActions/{postUrn}/comments
  Requires scope: w_member_social
  Supports nested replies via parentComment field

API Concepts (versioning, pagination, error codes):
  https://learn.microsoft.com/en-us/linkedin/shared/api-guide/concepts
  Header: LinkedIn-Version: YYYYMM  (active versions: 202609+)
  Header: X-Restli-Protocol-Version: 2.0.0

OAuth endpoints in this server
───────────────────────────────
GET  /oauth/start              → redirect user to LinkedIn authorization page
GET  /oauth/callback           → exchange code for token, persist to _token_store
GET  /oauth/status             → show token status (no secrets exposed)
GET  /oauth/reauthorize        → operator hint: explains fresh-scope reauth URL

MCP tools
──────────
linkedin_validate_token        → check token validity + expiry
linkedin_get_profile           → GET /v2/userinfo
linkedin_get_profile_posts     → GET /rest/posts?q=author
linkedin_create_post           → POST /rest/posts  (idempotent, returns post_urn)
linkedin_get_post              → GET /rest/posts/{id}
linkedin_get_publish_status    → idempotency registry lookup
linkedin_create_comment        → POST /rest/socialActions/{postUrn}/comments
linkedin_get_comments          → GET  /rest/socialActions/{postUrn}/comments
linkedin_create_comment_reply  → POST /rest/socialActions/{commentUrn}/comments (nested reply)
linkedin_enable_comments       → PATCH /rest/posts/{id} (toggle commentsRestricted=False)
linkedin_disable_comments      → PATCH /rest/posts/{id} (toggle commentsRestricted=True)
linkedin_get_audit             → return last 50 entries from the audit log

Publishing architecture for AIFeeders
  1. linkedin_create_post   → publishes main article as a LinkedIn post
                               returns post_urn (e.g. urn:li:share:1234567890)
  2. linkedin_create_comment × 5 → each persona perspective as a separate comment
                               returns comment_urn per persona
  Idempotency: post_urn and comment_urns are stored; CronJob re-runs skip already-published items.
  Comment serialization: LINKEDIN_COMMENT_DELAY_SECONDS (default 3) is awaited at the start of
  every linkedin_create_comment call so burst requests never hit LinkedIn's rate limits.

Error classification
  Every _do_create_post / _do_create_comment failure returns a structured LinkedInError dict:
    error_class: AUTH_ERROR | PERMISSION_ERROR | RATE_LIMITED | VALIDATION_ERROR |
                 SERVER_ERROR | NETWORK_ERROR
  Callers inspect "status" == "error" and "retry_eligible" to decide whether to retry.

Security design
  - Access tokens never reach agents or LLMs.
  - Narrowly scoped tools only — no generic execute.
  - Idempotency via publication_key — duplicate CronJob runs never double-post or double-comment.
  - 3000-char limit enforced for posts before any API call (LinkedIn hard limit).
  - 1250-char limit for comments (LinkedIn soft limit per Comments API docs).
  - PKCE-style state parameter validates OAuth callback to prevent CSRF.
  - Audit log never stores the access token.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.mcpserver import MCPServer as FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("LinkedIn MCP Server")

# ── Credentials (injected from OpenShift Secret / .env) ───────────────────────
# NEVER log, print, or return these values in responses
LINKEDIN_CLIENT_ID     = os.environ.get("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")

# Base access token from env — superseded by OAuth flow once completed
_ENV_ACCESS_TOKEN  = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
_ENV_REFRESH_TOKEN = os.environ.get("LINKEDIN_REFRESH_TOKEN", "")

# ── OAuth redirect URI (set to match LinkedIn app configuration) ───────────────
LINKEDIN_REDIRECT_URI = os.environ.get(
    "LINKEDIN_REDIRECT_URI",
    "http://localhost:8104/oauth/callback",
)

# ── Scopes — env-configurable so per-environment overrides need no rebuild.
# Must include w_member_social for posts + comments.
# Stored in OpenShift Secret (LINKEDIN_SCOPES) so it can differ across clusters.
# Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
LINKEDIN_SCOPES = os.environ.get("LINKEDIN_SCOPES", "openid profile email w_member_social")

# ── API version — env-configurable; default 202609 (202510 is sunset Sep 2026).
# Ref: https://learn.microsoft.com/en-us/linkedin/shared/api-guide/concepts
LINKEDIN_API_VERSION = os.environ.get("LINKEDIN_API_VERSION", "202609")

# ── Posts endpoint — overridable without rebuild (e.g., sandbox vs prod)
LINKEDIN_POSTS_ENDPOINT = os.environ.get(
    "LINKEDIN_POSTS_ENDPOINT",
    "https://api.linkedin.com/rest/posts",
)

# ── Comment serialization delay (seconds) — safety net against burst rates.
# Publisher agent already calls comments sequentially; this adds a floor delay
# at the MCP layer. Env: LINKEDIN_COMMENT_DELAY_SECONDS (default 3).
COMMENT_DELAY_SECONDS = float(os.environ.get("LINKEDIN_COMMENT_DELAY_SECONDS", "3"))

# ── OAuth endpoints (official docs) ───────────────────────────────────────────
LINKEDIN_AUTH_URL   = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL  = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_REVOKE_URL = "https://www.linkedin.com/oauth/v2/revoke"

# ── API base URLs ──────────────────────────────────────────────────────────────
LINKEDIN_REST_BASE = "https://api.linkedin.com/rest"
LINKEDIN_V2_BASE   = "https://api.linkedin.com/v2"
LINKEDIN_OIDC_BASE = "https://api.linkedin.com"

# Character limits (per LinkedIn API docs)
POST_MAX_CHARS    = 3000   # Posts API hard limit
COMMENT_MAX_CHARS = 1250   # Comments API recommended limit

# ── Audit log — last 50 publish/comment attempts (success + failure).
# Never stores access token or client secret.
_audit_log: deque[dict[str, Any]] = deque(maxlen=50)

# ── In-memory token store (production: persist encrypted in PostgreSQL / Vault) ─
_token_store: dict[str, Any] = {
    "access_token":       _ENV_ACCESS_TOKEN,
    "refresh_token":      _ENV_REFRESH_TOKEN,
    "expires_at":         0.0,
    "refresh_expires_at": 0.0,
    "scope":              LINKEDIN_SCOPES if _ENV_ACCESS_TOKEN else "",
    "obtained_at":        0.0,
}

# OAuth CSRF state registry: {state_token: {"created_at": float}}
_oauth_states: dict[str, dict[str, Any]] = {}

# ── In-process idempotency registries ────────────────────────────────────────
# Post registry:    publication_key → {"post_urn": str, "id": str, "status": str, ...}
# Comment registry: comment_key    → {"comment_urn": str, "status": str, ...}
_published_posts:    dict[str, dict[str, Any]] = {}
_published_comments: dict[str, dict[str, Any]] = {}

# Cache the profile URN after first successful fetch
_profile_urn_cache: str = ""


# ── Token accessors ───────────────────────────────────────────────────────────

def _get_access_token() -> str:
    return _token_store.get("access_token", "")


def _is_token_present() -> bool:
    return bool(_token_store.get("access_token", ""))


def _is_token_expired() -> bool:
    expires_at = _token_store.get("expires_at", 0.0)
    if expires_at == 0.0:
        return False
    return time.time() > expires_at


def _token_expiry_iso() -> str | None:
    expires_at = _token_store.get("expires_at", 0.0)
    if expires_at == 0.0:
        return None
    return datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()


def _store_token_response(data: dict[str, Any]) -> None:
    """
    Persist token exchange response to the in-memory store.
    LinkedIn token response fields:
      access_token, expires_in, refresh_token, refresh_token_expires_in, scope
    """
    now = time.time()
    _token_store["access_token"]       = data.get("access_token", "")
    _token_store["refresh_token"]      = data.get("refresh_token", "")
    _token_store["scope"]              = data.get("scope", "")
    _token_store["obtained_at"]        = now
    expires_in = data.get("expires_in")
    _token_store["expires_at"] = now + expires_in if expires_in else 0.0
    rt_expires = data.get("refresh_token_expires_in")
    _token_store["refresh_expires_at"] = now + rt_expires if rt_expires else 0.0
    global _profile_urn_cache
    _profile_urn_cache = ""
    logger.info("LinkedIn access token stored; expires_at=%s", _token_expiry_iso())


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _build_auth_url(state: str) -> str:
    params = {
        "response_type": "code",
        "client_id":     LINKEDIN_CLIENT_ID,
        "redirect_uri":  LINKEDIN_REDIRECT_URI,
        "scope":         LINKEDIN_SCOPES,
        "state":         state,
    }
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


async def _exchange_code_for_token(code: str) -> dict[str, Any]:
    payload = {
        "grant_type":    "authorization_code",
        "code":          code,
        "client_id":     LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET,
        "redirect_uri":  LINKEDIN_REDIRECT_URI,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            LINKEDIN_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


async def _refresh_access_token() -> dict[str, Any]:
    refresh_token = _token_store.get("refresh_token", "")
    if not refresh_token:
        raise ValueError("No refresh token available")
    payload = {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     LINKEDIN_CLIENT_ID,
        "client_secret": LINKEDIN_CLIENT_SECRET,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            LINKEDIN_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


# ── Shared auth headers ────────────────────────────────────────────────────────

def _rest_headers() -> dict[str, str]:
    """Headers for /rest/* endpoints (Posts API, Comments API)."""
    return {
        "Authorization":             f"Bearer {_get_access_token()}",
        "LinkedIn-Version":          LINKEDIN_API_VERSION,
        "Content-Type":              "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _v2_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_get_access_token()}"}


# ── Error classification ───────────────────────────────────────────────────────

def _classify_http_error(
    exc: httpx.HTTPStatusError,
    *,
    actor_urn: str,
    endpoint: str,
    attempt: int,
    tool: str,
    post_urn: str = "",
) -> dict[str, Any]:
    """
    Convert an httpx.HTTPStatusError into a structured LinkedIn error dict.

    error_class values:
      AUTH_ERROR        — 401 (token missing / expired)
      PERMISSION_ERROR  — 403 (scope not granted, e.g. w_member_social missing)
      RATE_LIMITED      — 429
      VALIDATION_ERROR  — 400, 422 (bad payload)
      SERVER_ERROR      — 5xx
      NETWORK_ERROR     — transport errors (caught separately)

    retry_eligible is True only for 429 and 5xx — callers should back off and retry.
    """
    status = exc.response.status_code
    try:
        body = exc.response.json()
    except Exception:
        body = {}

    li_error_code    = body.get("code", "")
    li_service_error = body.get("serviceErrorCode", 0)
    li_message       = body.get("message", exc.response.text[:300])
    li_request_id    = exc.response.headers.get("x-li-uuid", "")

    if status == 401:
        error_class = "AUTH_ERROR"
    elif status == 403:
        error_class = "PERMISSION_ERROR"
    elif status == 429:
        error_class = "RATE_LIMITED"
    elif status in (400, 422):
        error_class = "VALIDATION_ERROR"
    elif status >= 500:
        error_class = "SERVER_ERROR"
    else:
        error_class = "VALIDATION_ERROR"

    retry_eligible = status in (429,) or status >= 500

    logger.error(
        "%s failed [%s] HTTP %s | li_code=%s | li_svc_err=%s | msg=%s | req_id=%s | version=%s",
        tool, error_class, status, li_error_code, li_service_error,
        li_message[:120], li_request_id, LINKEDIN_API_VERSION,
    )

    return {
        "status":          "error",
        "error_class":     error_class,
        "http_status":     status,
        "li_error_code":   li_error_code,
        "li_service_error": li_service_error,
        "li_message":      li_message,
        "li_request_id":   li_request_id,
        "li_version_used": LINKEDIN_API_VERSION,
        "endpoint":        endpoint,
        "actor_urn":       actor_urn,
        "attempt":         attempt,
        "retry_eligible":  retry_eligible,
        "post_urn":        post_urn,
    }


def _classify_network_error(
    exc: Exception,
    *,
    actor_urn: str,
    endpoint: str,
    attempt: int,
    tool: str,
    post_urn: str = "",
) -> dict[str, Any]:
    """Convert a transport-level exception into a structured error dict."""
    logger.error("%s network error: %s", tool, exc)
    return {
        "status":          "error",
        "error_class":     "NETWORK_ERROR",
        "http_status":     0,
        "li_error_code":   "",
        "li_service_error": 0,
        "li_message":      str(exc),
        "li_request_id":   "",
        "li_version_used": LINKEDIN_API_VERSION,
        "endpoint":        endpoint,
        "actor_urn":       actor_urn,
        "attempt":         attempt,
        "retry_eligible":  True,
        "post_urn":        post_urn,
    }


# ── Audit helpers ─────────────────────────────────────────────────────────────

def _audit(
    *,
    tool: str,
    actor_urn: str,
    post_urn: str,
    status: str,
    error_class: str = "",
    http_status: int = 0,
    li_message: str = "",
    li_request_id: str = "",
    attempt: int = 1,
) -> None:
    """Append one entry to the audit log. Never logs the access token."""
    _audit_log.append({
        "timestamp":    datetime.now(tz=timezone.utc).isoformat(),
        "tool":         tool,
        "actor_urn":    actor_urn,
        "post_urn":     post_urn,
        "status":       status,
        "error_class":  error_class,
        "http_status":  http_status,
        "li_message":   li_message,
        "li_request_id": li_request_id,
        "attempt":      attempt,
    })


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _fetch_userinfo() -> dict[str, Any]:
    """GET /v2/userinfo — requires openid profile email scopes."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{LINKEDIN_OIDC_BASE}/v2/userinfo",
            headers=_v2_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def _get_profile_urn() -> str:
    """
    Return the person URN (urn:li:person:XXXX) for the authenticated user.
    Cached after first call. Falls back to mock URN if token is invalid.
    """
    global _profile_urn_cache
    if _profile_urn_cache:
        return _profile_urn_cache
    if not _is_token_present():
        return "urn:li:person:mock-profile"
    try:
        info = await _fetch_userinfo()
        sub = info.get("sub", "")
        _profile_urn_cache = f"urn:li:person:{sub}" if sub else "urn:li:person:mock-profile"
    except httpx.HTTPStatusError as exc:
        logger.warning("LinkedIn userinfo failed (%s) — using mock URN", exc.response.status_code)
        _profile_urn_cache = "urn:li:person:mock-profile"
    return _profile_urn_cache


async def _do_create_post(author_urn: str, text: str, attempt: int = 1) -> dict[str, Any]:
    """
    POST /rest/posts — Share on LinkedIn (Posts API).
    Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api

    Required body fields:
      author, commentary, visibility, distribution, lifecycleState, isReshareDisabledByAuthor

    Returns: {"post_urn": str, "id": str, "status": "published"|"mock"}
    On success LinkedIn returns HTTP 201 (or 200 for some variants); post URN in x-restli-id header.
    Failures return a structured error dict (see _classify_http_error).
    """
    endpoint = LINKEDIN_POSTS_ENDPOINT

    if not _is_token_present():
        mock_id  = "mock-post-" + hashlib.md5(text.encode()).hexdigest()[:8]
        mock_urn = f"urn:li:share:{mock_id}"
        logger.info("[MOCK] LinkedIn post created: %s", mock_urn)
        _audit(tool="_do_create_post", actor_urn=author_urn, post_urn=mock_urn,
               status="mock", attempt=attempt)
        return {"post_urn": mock_urn, "id": mock_id, "status": "mock"}

    payload = {
        "author":      author_urn,
        "commentary":  text,
        "visibility":  "PUBLIC",
        "distribution": {
            "feedDistribution":               "MAIN_FEED",
            "targetEntities":                 [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState":            "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(endpoint, headers=_rest_headers(), json=payload)

        # Accept both 201 (standard) and 200 (some LinkedIn API variants)
        if resp.status_code not in (200, 201):
            resp.raise_for_status()

        # Primary URN source: x-restli-id header (LinkedIn 201 response)
        raw_id   = resp.headers.get("x-restli-id", "")
        post_urn = raw_id if raw_id.startswith("urn:") else (
            f"urn:li:share:{raw_id}" if raw_id else ""
        )
        logger.info("LinkedIn post published: %s (HTTP %s)", post_urn, resp.status_code)
        _audit(tool="_do_create_post", actor_urn=author_urn, post_urn=post_urn,
               status="published", http_status=resp.status_code, attempt=attempt)
        return {"post_urn": post_urn, "id": raw_id, "status": "published"}

    except httpx.HTTPStatusError as exc:
        err = _classify_http_error(
            exc, actor_urn=author_urn, endpoint=endpoint,
            attempt=attempt, tool="_do_create_post",
        )
        _audit(
            tool="_do_create_post", actor_urn=author_urn, post_urn="",
            status="error", error_class=err["error_class"],
            http_status=err["http_status"], li_message=err["li_message"],
            li_request_id=err["li_request_id"], attempt=attempt,
        )
        return err
    except (httpx.RequestError, OSError) as exc:
        err = _classify_network_error(
            exc, actor_urn=author_urn, endpoint=endpoint,
            attempt=attempt, tool="_do_create_post",
        )
        _audit(
            tool="_do_create_post", actor_urn=author_urn, post_urn="",
            status="error", error_class="NETWORK_ERROR",
            http_status=0, li_message=str(exc), attempt=attempt,
        )
        return err


async def _do_create_comment(
    post_urn: str,
    actor_urn: str,
    text: str,
    parent_comment_urn: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """
    POST /rest/socialActions/{postUrn}/comments — Comments API.
    Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api

    For top-level comments: post_urn = urn:li:share:{id} or urn:li:ugcPost:{id}
    For nested replies:     post_urn = urn:li:share:{id}, parent_comment_urn = urn:li:comment:(...)

    Returns: {"comment_urn": str, "status": "published"|"mock"}
    On success LinkedIn returns HTTP 201 (or 200); comment URN in x-restli-id header.
    Failures return a structured error dict (see _classify_http_error).
    """
    encoded_post_urn = quote(post_urn, safe="")
    endpoint = f"{LINKEDIN_REST_BASE}/socialActions/{encoded_post_urn}/comments"

    if not _is_token_present():
        mock_id      = "mock-comment-" + hashlib.md5(text.encode()).hexdigest()[:8]
        comment_urn  = f"urn:li:comment:{mock_id}"
        logger.info("[MOCK] LinkedIn comment created: %s", comment_urn)
        _audit(tool="_do_create_comment", actor_urn=actor_urn, post_urn=post_urn,
               status="mock", attempt=attempt)
        return {"comment_urn": comment_urn, "status": "mock"}

    payload: dict[str, Any] = {
        "actor":   actor_urn,
        "message": {"text": text},
    }

    # Include parentComment for nested replies
    # Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api
    if parent_comment_urn:
        payload["parentComment"] = parent_comment_urn

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(endpoint, headers=_rest_headers(), json=payload)

        # Accept both 201 (standard) and 200
        if resp.status_code not in (200, 201):
            resp.raise_for_status()

        raw_id      = resp.headers.get("x-restli-id", "")
        comment_urn = raw_id if raw_id.startswith("urn:") else (
            f"urn:li:comment:{raw_id}" if raw_id else ""
        )
        logger.info("LinkedIn comment created: %s on %s (HTTP %s)",
                    comment_urn, post_urn, resp.status_code)
        _audit(tool="_do_create_comment", actor_urn=actor_urn, post_urn=post_urn,
               status="published", http_status=resp.status_code, attempt=attempt)
        return {"comment_urn": comment_urn, "status": "published"}

    except httpx.HTTPStatusError as exc:
        err = _classify_http_error(
            exc, actor_urn=actor_urn, endpoint=endpoint,
            attempt=attempt, tool="_do_create_comment", post_urn=post_urn,
        )
        _audit(
            tool="_do_create_comment", actor_urn=actor_urn, post_urn=post_urn,
            status="error", error_class=err["error_class"],
            http_status=err["http_status"], li_message=err["li_message"],
            li_request_id=err["li_request_id"], attempt=attempt,
        )
        return err
    except (httpx.RequestError, OSError) as exc:
        err = _classify_network_error(
            exc, actor_urn=actor_urn, endpoint=endpoint,
            attempt=attempt, tool="_do_create_comment", post_urn=post_urn,
        )
        _audit(
            tool="_do_create_comment", actor_urn=actor_urn, post_urn=post_urn,
            status="error", error_class="NETWORK_ERROR",
            http_status=0, li_message=str(exc), attempt=attempt,
        )
        return err


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def linkedin_validate_token() -> dict:
    """
    Check the validity and expiry of the current LinkedIn access token.
    Does NOT make a LinkedIn API call — reads from the local token store.
    """
    present     = _is_token_present()
    expired     = _is_token_expired()
    has_refresh = bool(_token_store.get("refresh_token", ""))
    obtained_at = _token_store.get("obtained_at", 0.0)
    source = "none"
    if present:
        source = "oauth" if obtained_at > 0 else "env"

    return {
        "present":     present,
        "expired":     expired,
        "expires_at":  _token_expiry_iso(),
        "scope":       _token_store.get("scope", ""),
        "has_refresh": has_refresh,
        "source":      source,
        "status":      "ok" if (present and not expired) else ("expired" if expired else "no_token"),
    }


@mcp.tool()
async def linkedin_get_profile() -> dict:
    """
    Fetch the authenticated LinkedIn member's profile via GET /v2/userinfo.
    Scope required: openid profile email
    """
    if not _is_token_present():
        return {
            "id": "mock-id", "urn": "urn:li:person:mock-profile",
            "name": "Mock User", "email": "mock@example.com", "status": "mock",
        }
    try:
        userinfo = await _fetch_userinfo()
    except httpx.HTTPStatusError as exc:
        logger.error("LinkedIn profile fetch failed: %s", exc)
        return {"error": str(exc), "status": "error"}

    sub = userinfo.get("sub", "")
    global _profile_urn_cache
    if sub:
        _profile_urn_cache = f"urn:li:person:{sub}"

    return {
        "id":      sub,
        "urn":     f"urn:li:person:{sub}" if sub else "",
        "name":    userinfo.get("name", ""),
        "email":   userinfo.get("email", ""),
        "picture": userinfo.get("picture", ""),
        "status":  "ok",
    }


@mcp.tool()
async def linkedin_get_profile_posts(count: int = 10) -> dict:
    """
    Retrieve recent posts by the authenticated author.
    GET /rest/posts?author={urn}&q=author
    Scope required: w_member_social
    """
    if not _is_token_present():
        return {"posts": [], "total": 0, "status": "mock"}

    author_urn = await _get_profile_urn()
    params = {"author": author_urn, "q": "author", "count": min(count, 50), "sortBy": "LAST_MODIFIED"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{LINKEDIN_REST_BASE}/posts",
                headers=_rest_headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("linkedin_get_profile_posts failed: %s", exc)
        return {"error": str(exc), "status": "error"}

    elements = data.get("elements", [])
    posts = [
        {
            "id":             el.get("id", ""),
            "text":           el.get("commentary", ""),
            "visibility":     el.get("visibility", ""),
            "lifecycleState": el.get("lifecycleState", ""),
            "publishedAt":    el.get("publishedAt", ""),
        }
        for el in elements
    ]
    return {"posts": posts, "total": data.get("paging", {}).get("total", len(posts)), "status": "ok"}


@mcp.tool()
async def linkedin_create_post(text: str, publication_key: str) -> dict:
    """
    Share a text post on LinkedIn (Posts API) with idempotency enforcement.
    POST /rest/posts
    Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api

    Returns post_urn which must be supplied to linkedin_create_comment for persona comments.

    Scope required: w_member_social
    Char limit: 3000
    """
    if publication_key in _published_posts:
        logger.info("Duplicate post suppressed: %s", publication_key)
        return {**_published_posts[publication_key], "idempotent": True}

    if len(text) > POST_MAX_CHARS:
        return {"error": f"Post text is {len(text)} chars — exceeds {POST_MAX_CHARS}-char limit."}

    author_urn = await _get_profile_urn()
    result = await _do_create_post(author_urn, text)

    _published_posts[publication_key] = {
        **result,
        "publication_key": publication_key,
        "author_urn":      author_urn,
        "text_length":     len(text),
    }
    return _published_posts[publication_key]


@mcp.tool()
async def linkedin_get_post(post_id: str) -> dict:
    """
    Retrieve a post by its ID.
    GET /rest/posts/{id}
    Scope required: w_member_social
    """
    if not _is_token_present():
        return {"post_id": post_id, "status": "mock"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{LINKEDIN_REST_BASE}/posts/{post_id}",
                headers=_rest_headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("linkedin_get_post %s failed: %s", post_id, exc)
        return {"error": str(exc), "status": "error"}


@mcp.tool()
async def linkedin_get_publish_status(post_id: str) -> dict:
    """
    Check publication status from the local idempotency registry. No API call made.
    """
    for entry in _published_posts.values():
        if entry.get("id") == post_id or entry.get("post_urn", "").endswith(post_id):
            return {"post_id": post_id, "status": "published", "data": entry}
    return {"post_id": post_id, "status": "unknown"}


@mcp.tool()
async def linkedin_create_comment(
    post_urn: str,
    text: str,
    comment_key: str,
) -> dict:
    """
    Add a comment to a LinkedIn post (Comments API).
    POST /rest/socialActions/{postUrn}/comments
    Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api

    post_urn    — URN returned by linkedin_create_post (e.g. urn:li:share:1234567890)
    text        — comment text (max 1250 chars recommended)
    comment_key — idempotency key (e.g. "{publication_key}:business")

    A delay of LINKEDIN_COMMENT_DELAY_SECONDS (default 3s) is applied at the start of every
    call as a rate-limit safety net, regardless of whether the publisher agent serializes calls.

    Returns comment_urn for use in nested replies or tracking.
    Scope required: w_member_social
    """
    # Serialization safety net — rate-limit floor between consecutive comments.
    # Idempotent calls skip the delay to avoid penalizing retries.
    if comment_key in _published_comments:
        logger.info("Duplicate comment suppressed: %s", comment_key)
        return {**_published_comments[comment_key], "idempotent": True}

    await asyncio.sleep(COMMENT_DELAY_SECONDS)

    if len(text) > COMMENT_MAX_CHARS:
        text = text[:COMMENT_MAX_CHARS - 3] + "..."
        logger.warning("Comment truncated to %d chars for key: %s", COMMENT_MAX_CHARS, comment_key)

    actor_urn = await _get_profile_urn()
    result = await _do_create_comment(post_urn, actor_urn, text)

    _published_comments[comment_key] = {
        **result,
        "comment_key": comment_key,
        "post_urn":    post_urn,
        "actor_urn":   actor_urn,
        "text_length": len(text),
    }
    return _published_comments[comment_key]


@mcp.tool()
async def linkedin_get_comments(post_urn: str, count: int = 20) -> dict:
    """
    Retrieve comments on a LinkedIn post.
    GET /rest/socialActions/{postUrn}/comments
    Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api

    Scope required: w_member_social
    """
    if not _is_token_present():
        return {"comments": [], "total": 0, "status": "mock"}

    encoded_urn = quote(post_urn, safe="")
    url = f"{LINKEDIN_REST_BASE}/socialActions/{encoded_urn}/comments"
    params = {"count": min(count, 100), "start": 0}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=_rest_headers(), params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("linkedin_get_comments failed for %s: %s", post_urn, exc)
        return {"error": str(exc), "status": "error"}

    elements = data.get("elements", [])
    comments = [
        {
            "id":          el.get("id", ""),
            "comment_urn": el.get("$URN", ""),
            "text":        el.get("message", {}).get("text", ""),
            "actor":       el.get("actor", ""),
            "created":     el.get("created", {}).get("time", ""),
            "like_count":  el.get("likesSummary", {}).get("totalLikes", 0),
        }
        for el in elements
    ]
    return {
        "post_urn": post_urn,
        "comments": comments,
        "total":    data.get("paging", {}).get("total", len(comments)),
        "status":   "ok",
    }


@mcp.tool()
async def linkedin_create_comment_reply(
    post_urn: str,
    parent_comment_urn: str,
    text: str,
    reply_key: str,
) -> dict:
    """
    Reply to a comment on a LinkedIn post (nested comment).
    POST /rest/socialActions/{postUrn}/comments  with parentComment field
    Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api

    post_urn           — URN of the top-level post (urn:li:share:...)
    parent_comment_urn — URN of the comment being replied to
    text               — reply text (max 1250 chars)
    reply_key          — idempotency key

    Scope required: w_member_social
    """
    if reply_key in _published_comments:
        logger.info("Duplicate reply suppressed: %s", reply_key)
        return {**_published_comments[reply_key], "idempotent": True}

    if len(text) > COMMENT_MAX_CHARS:
        text = text[:COMMENT_MAX_CHARS - 3] + "..."

    actor_urn = await _get_profile_urn()
    result = await _do_create_comment(post_urn, actor_urn, text, parent_comment_urn)

    _published_comments[reply_key] = {
        **result,
        "reply_key":          reply_key,
        "post_urn":           post_urn,
        "parent_comment_urn": parent_comment_urn,
        "actor_urn":          actor_urn,
    }
    return _published_comments[reply_key]


@mcp.tool()
async def linkedin_enable_comments(post_urn: str) -> dict:
    """
    Enable comments on a post by updating its commentsRestricted field.
    PATCH /rest/posts/{id}
    Scope required: w_member_social
    """
    if not _is_token_present():
        return {"post_urn": post_urn, "status": "mock"}

    post_id = post_urn.split(":")[-1]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{LINKEDIN_REST_BASE}/posts/{post_id}",
                headers={
                    **_rest_headers(),
                    "X-Restli-Method": "partial_update",
                },
                json={"patch": {"$set": {"commentsRestricted": False}}},
            )
            resp.raise_for_status()
            return {"post_urn": post_urn, "comments_enabled": True, "status": "ok"}
    except httpx.HTTPStatusError as exc:
        logger.error("linkedin_enable_comments failed: %s", exc)
        return {"error": str(exc), "status": "error"}


@mcp.tool()
async def linkedin_disable_comments(post_urn: str) -> dict:
    """
    Disable comments on a post.
    PATCH /rest/posts/{id}
    Scope required: w_member_social
    """
    if not _is_token_present():
        return {"post_urn": post_urn, "status": "mock"}

    post_id = post_urn.split(":")[-1]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{LINKEDIN_REST_BASE}/posts/{post_id}",
                headers={
                    **_rest_headers(),
                    "X-Restli-Method": "partial_update",
                },
                json={"patch": {"$set": {"commentsRestricted": True}}},
            )
            resp.raise_for_status()
            return {"post_urn": post_urn, "comments_enabled": False, "status": "ok"}
    except httpx.HTTPStatusError as exc:
        logger.error("linkedin_disable_comments failed: %s", exc)
        return {"error": str(exc), "status": "error"}


@mcp.tool()
async def linkedin_get_audit() -> dict:
    """
    Return the last 50 publish/comment attempts from the in-process audit log.
    Each entry: {timestamp, tool, actor_urn, post_urn, status, error_class,
                 http_status, li_message, li_request_id, attempt}
    The access token is never included.
    """
    return {
        "entries": list(_audit_log),
        "count":   len(_audit_log),
        "status":  "ok",
    }


# ── Tool registry ─────────────────────────────────────────────────────────────
_TOOLS = {
    # Token / profile
    "linkedin_validate_token":       linkedin_validate_token,
    "linkedin_get_profile":          linkedin_get_profile,
    "linkedin_get_profile_posts":    linkedin_get_profile_posts,
    # Posts
    "linkedin_create_post":          linkedin_create_post,
    "linkedin_get_post":             linkedin_get_post,
    "linkedin_get_publish_status":   linkedin_get_publish_status,
    # Comments
    "linkedin_create_comment":       linkedin_create_comment,
    "linkedin_get_comments":         linkedin_get_comments,
    "linkedin_create_comment_reply": linkedin_create_comment_reply,
    # Comment controls
    "linkedin_enable_comments":      linkedin_enable_comments,
    "linkedin_disable_comments":     linkedin_disable_comments,
    # Audit
    "linkedin_get_audit":            linkedin_get_audit,
    # dot-notation aliases for ergonomic use from agents
    "linkedin.validate_token":       linkedin_validate_token,
    "linkedin.get_profile":          linkedin_get_profile,
    "linkedin.create_post":          linkedin_create_post,
    "linkedin.get_post":             linkedin_get_post,
    "linkedin.get_publish_status":   linkedin_get_publish_status,
    "linkedin.create_comment":       linkedin_create_comment,
    "linkedin.get_comments":         linkedin_get_comments,
    "linkedin.create_comment_reply": linkedin_create_comment_reply,
    "linkedin.get_audit":            linkedin_get_audit,
}


# ── FastAPI app: health + OAuth routes + /call dispatcher ─────────────────────

_app = FastAPI(title="LinkedIn MCP Server")
_STATE_TTL_SECONDS = 600


def _scopes_sufficient() -> bool:
    """True if w_member_social appears in both the configured and granted scopes."""
    configured = LINKEDIN_SCOPES
    granted    = _token_store.get("scope", "")
    return "w_member_social" in configured and "w_member_social" in granted


@_app.get("/health")
def health():
    last_post = _audit_log[-1] if _audit_log else None
    return {
        "status":               "healthy",
        "server":               "linkedin-mcp",
        "token_configured":     _is_token_present(),
        "token_expired":        _is_token_expired(),
        "token_scopes_granted": _token_store.get("scope", ""),
        "scopes_configured":    LINKEDIN_SCOPES,
        "scopes_sufficient":    _scopes_sufficient(),
        "api_version":          LINKEDIN_API_VERSION,
        "posts_endpoint":       LINKEDIN_POSTS_ENDPOINT,
        "comment_delay_s":      COMMENT_DELAY_SECONDS,
        "last_post_status":     last_post,
        "tools":                list(_TOOLS.keys()),
        "oauth": {
            "start_url":        "/oauth/start",
            "callback_url":     "/oauth/callback",
            "status_url":       "/oauth/status",
            "reauthorize_url":  "/oauth/reauthorize",
            "redirect_uri":     LINKEDIN_REDIRECT_URI,
        },
        "endpoints": {
            "posts_create":    LINKEDIN_POSTS_ENDPOINT,
            "posts_list":      f"{LINKEDIN_REST_BASE}/posts?q=author",
            "comments_create": f"{LINKEDIN_REST_BASE}/socialActions/{{postUrn}}/comments",
            "comments_get":    f"{LINKEDIN_REST_BASE}/socialActions/{{postUrn}}/comments",
            "userinfo":        f"{LINKEDIN_OIDC_BASE}/v2/userinfo",
        },
    }


@_app.get("/audit")
def audit_endpoint():
    """
    GET /audit — returns the last 50 publish/comment attempts.
    Useful for operators diagnosing publish failures without tailing logs.
    """
    return {
        "entries": list(_audit_log),
        "count":   len(_audit_log),
    }


@_app.post("/call")
async def call_tool(request: dict):
    """Simple REST tool dispatcher. Body: {"tool": "<name>", "arguments": {...}}"""
    import inspect
    tool_name = request.get("tool", "")
    fn = _TOOLS.get(tool_name)
    if fn is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown tool: {tool_name!r}. Available: {sorted(set(_TOOLS.keys()))}",
        )
    args = request.get("arguments", {})
    result = await fn(**args) if inspect.iscoroutinefunction(fn) else fn(**args)
    return {"result": result}


@_app.get("/oauth/start")
def oauth_start():
    """
    Step 1 of LinkedIn 3-legged OAuth.
    Generates a random state token, stores it, then redirects to LinkedIn authorization page.
    Ref: https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow
    """
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="LINKEDIN_CLIENT_ID is not configured.",
        )
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"created_at": time.time()}
    now = time.time()
    expired = [k for k, v in _oauth_states.items() if now - v["created_at"] > _STATE_TTL_SECONDS]
    for k in expired:
        del _oauth_states[k]

    auth_url = _build_auth_url(state)
    logger.info("OAuth flow started; redirecting to LinkedIn")
    return RedirectResponse(url=auth_url, status_code=302)


@_app.get("/oauth/callback")
async def oauth_callback(
    code:              str = Query(None),
    state:             str = Query(None),
    error:             str = Query(None),
    error_description: str = Query(None),
):
    """
    Step 2 of LinkedIn 3-legged OAuth — callback handler.
    Validates state, exchanges code for token, persists to _token_store.
    """
    if error:
        logger.warning("LinkedIn OAuth denied: %s — %s", error, error_description)
        return HTMLResponse(content=_oauth_denied_html(error, error_description or ""), status_code=200)

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter.")

    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid or expired state token. Visit /oauth/start to restart.")
    del _oauth_states[state]

    try:
        token_data = await _exchange_code_for_token(code)
    except httpx.HTTPStatusError as exc:
        logger.error("Token exchange failed: %s — %s", exc.response.status_code, exc.response.text)
        raise HTTPException(status_code=502, detail=f"LinkedIn token exchange failed: {exc.response.status_code}")

    _store_token_response(token_data)
    logger.info("LinkedIn OAuth flow completed successfully; scopes=%s", _token_store.get("scope", ""))
    return HTMLResponse(content=_oauth_success_html(), status_code=200)


@_app.get("/oauth/status")
def oauth_status():
    """Return current OAuth token status (no secrets exposed)."""
    return {
        "token_present":    _is_token_present(),
        "token_expired":    _is_token_expired(),
        "expires_at":       _token_expiry_iso(),
        "scope":            _token_store.get("scope", ""),
        "scopes_configured": LINKEDIN_SCOPES,
        "scopes_sufficient": _scopes_sufficient(),
        "has_refresh":      bool(_token_store.get("refresh_token", "")),
        "oauth_start":      "/oauth/start",
        "oauth_reauth":     "/oauth/reauthorize",
        "oauth_docs":       "https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow",
        "client_id_set":    bool(LINKEDIN_CLIENT_ID),
        "redirect_uri":     LINKEDIN_REDIRECT_URI,
        "required_scopes":  LINKEDIN_SCOPES,
        "api_version":      LINKEDIN_API_VERSION,
    }


@_app.post("/oauth/refresh")
async def oauth_refresh():
    """Attempt to refresh the access token using the stored refresh token."""
    try:
        token_data = await _refresh_access_token()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Token refresh failed: {exc.response.status_code}")
    _store_token_response(token_data)
    return {
        "refreshed":   True,
        "expires_at":  _token_expiry_iso(),
        "scope":       _token_store.get("scope", ""),
        "has_refresh": bool(_token_store.get("refresh_token", "")),
    }


@_app.get("/oauth/reauthorize")
def oauth_reauthorize():
    """
    Operator hint endpoint.
    Returns JSON explaining that the token needs fresh scopes (particularly w_member_social),
    along with the URL the operator must visit to reauthorize.

    Common cause: token was issued with only 'email openid profile' — w_member_social was
    never granted because the LinkedIn app permissions were not configured before the token
    was generated.  Fix: visit the oauth_start_url below, re-grant all scopes, then replace
    LINKEDIN_ACCESS_TOKEN in the OpenShift Secret with the newly issued token.
    """
    return {
        "action_required": "reauthorize",
        "reason": (
            "The current access token is missing w_member_social scope. "
            "LinkedIn returns ACCESS_DENIED on posts/comments API calls until this scope is granted. "
            "Visit oauth_start_url to start a fresh OAuth flow and grant all required scopes."
        ),
        "token_scopes_granted": _token_store.get("scope", "(no token)"),
        "scopes_required":      LINKEDIN_SCOPES,
        "scopes_sufficient":    _scopes_sufficient(),
        "oauth_start_url":      "/oauth/start",
        "linkedin_app_docs":    "https://www.linkedin.com/developers/apps",
        "scope_docs":           "https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api",
    }


# ── OAuth HTML pages ──────────────────────────────────────────────────────────

def _oauth_success_html() -> str:
    expiry  = _token_expiry_iso() or "unknown (check /oauth/status)"
    scope   = _token_store.get("scope", "")
    warning = ""
    if "w_member_social" not in scope:
        warning = (
            '<p style="background:#fff8c5;border:1px solid #d4a72c;padding:10px;border-radius:6px">'
            "⚠️ <strong>w_member_social was NOT granted.</strong> "
            "Posts and comments will fail with ACCESS_DENIED. "
            "Check your LinkedIn app permissions and re-authorize."
            "</p>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>LinkedIn OAuth — Success</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:60px auto;padding:0 20px;color:#1f2328}}
h1{{color:#1a7f37}}pre{{background:#f6f8fa;padding:12px;border-radius:6px;font-size:13px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;background:#dafbe1;color:#1a7f37}}
</style></head>
<body>
<h1>✅ LinkedIn Authorization Successful</h1>
{warning}
<p>Your LinkedIn access token has been stored. The AIFeeders workflow can now publish posts and persona comments.</p>
<table style="border-collapse:collapse;width:100%">
  <tr><td style="padding:6px;font-weight:bold">Token expires</td>
      <td style="padding:6px"><code>{expiry}</code></td></tr>
  <tr><td style="padding:6px;font-weight:bold">Scopes granted</td>
      <td style="padding:6px"><code>{scope}</code></td></tr>
  <tr><td style="padding:6px;font-weight:bold">Scopes required</td>
      <td style="padding:6px"><code>{LINKEDIN_SCOPES}</code></td></tr>
  <tr><td style="padding:6px;font-weight:bold">API version</td>
      <td style="padding:6px"><code>{LINKEDIN_API_VERSION}</code></td></tr>
</table>
<h2>What this enables</h2>
<ul>
  <li><strong>linkedin_create_post</strong> — publish the main AI news article</li>
  <li><strong>linkedin_create_comment × 5</strong> — add Business, Labor, Policy, Gen Z, Professional perspectives as comments</li>
  <li><strong>linkedin_create_comment_reply</strong> — add nested replies to any comment</li>
</ul>
<h2>Next steps</h2>
<ol>
  <li>Check status: <a href="/oauth/status"><code>GET /oauth/status</code></a></li>
  <li>For production persistence: copy the token to your <strong>OpenShift Secret</strong>
      as <code>LINKEDIN_ACCESS_TOKEN</code>.</li>
  <li>Trigger workflow: <code>POST /workflow/daily-news</code> on the API service.</li>
</ol>
<p style="color:#57606a;font-size:13px">
  Token is valid ~60 days. Re-authorize before expiry, or enable
  <a href="https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens"
     target="_blank">programmatic refresh tokens</a>.
</p>
</body></html>"""


def _oauth_denied_html(error: str, description: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>LinkedIn OAuth — Denied</title>
<style>body{{font-family:system-ui,sans-serif;max-width:600px;margin:60px auto;padding:0 20px;color:#1f2328}}
h1{{color:#cf222e}}</style></head>
<body>
<h1>❌ LinkedIn Authorization Denied</h1>
<p>The LinkedIn authorization was not completed.</p>
<table style="border-collapse:collapse">
  <tr><td style="padding:6px;font-weight:bold">Error</td>
      <td style="padding:6px"><code>{error}</code></td></tr>
  <tr><td style="padding:6px;font-weight:bold">Description</td>
      <td style="padding:6px">{description}</td></tr>
</table>
<p>To try again, visit <a href="/oauth/start"><code>GET /oauth/start</code></a>.</p>
</body></html>"""


_app.mount("/mcp", mcp.streamable_http_app())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(_app, host="0.0.0.0", port=8000)
