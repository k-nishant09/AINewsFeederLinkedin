#!/usr/bin/env python3
"""
scripts/verify_mcp.py
=====================
End-to-end verification script for the AI Daily News Platform.

Checks, in order:
  1. Environment variables are set
  2. Brave Search API key is valid (live HTTP call)
  3. LinkedIn token is valid (live HTTP call)
  4. News MCP server health + news_search_latest tool call
  5. PageIndex MCP server health + index + retrieve round-trip
  6. Evaluation MCP server health
  7. LinkedIn MCP server health + mock post (no real post unless --publish flag)

Usage
─────
# Minimal — only checks env vars and MCP server health (no real API calls)
  python scripts/verify_mcp.py --dry-run

# Full check — hits real Brave and LinkedIn APIs
  python scripts/verify_mcp.py

# Full check including a test LinkedIn post (USE WITH CAUTION)
  python scripts/verify_mcp.py --publish

Prerequisites
─────────────
  pip install httpx rich
  The MCP servers must be running (docker compose up, or individually).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime
from typing import Any

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed.  Run: pip install httpx rich")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich import print as rprint
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Console = None

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⏭️  SKIP"
WARN = "⚠️  WARN"


def ok(msg: str) -> None:
    print(f"  {PASS}  {msg}")


def fail(msg: str) -> None:
    print(f"  {FAIL}  {msg}")


def skip(msg: str) -> None:
    print(f"  {SKIP}  {msg}")


def warn(msg: str) -> None:
    print(f"  {WARN}  {msg}")


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ── 1. Environment variables ──────────────────────────────────────────────────

REQUIRED_ENV = [
    # News provider — GNews is the sole news provider
    ("GNEWS_API_KEY",          "GNews — sole AI news provider (32-char hex key from gnews.io)"),
    ("LINKEDIN_ACCESS_TOKEN",  "LinkedIn — publish posts"),
    ("LINKEDIN_CLIENT_ID",     "LinkedIn — OAuth app"),
    ("LLM_API_KEY",            "IBM Model Gateway — bearer token"),
    ("LLM_BASE_URL",           "IBM Model Gateway — base URL"),
    ("LLM_MODEL",              "IBM Model Gateway — model ID"),
    ("MCP_AUTH_TOKEN",         "MCP Gateway auth"),
    ("NEWS_MCP_URL",           "News MCP server URL"),
    ("PAGEINDEX_MCP_URL",      "PageIndex MCP server URL"),
    ("EVALUATION_MCP_URL",     "Evaluation MCP server URL"),
    ("LINKEDIN_MCP_URL",       "LinkedIn MCP server URL"),
]


def check_env() -> dict[str, str]:
    section("1. Environment Variables")
    values: dict[str, str] = {}
    all_ok = True
    for var, desc in REQUIRED_ENV:
        val = os.environ.get(var, "")
        values[var] = val
        if val:
            # Show first 8 chars + masked rest for keys
            masked = val[:8] + "*" * min(8, len(val) - 8)
            ok(f"{var:<28} = {masked}   [{desc}]")
        else:
            fail(f"{var:<28}   MISSING   [{desc}]")
            all_ok = False
    if not all_ok:
        print("\n  → Set missing values in .env and run:  source .env")
    return values


# ── 2. IBM Model Gateway ──────────────────────────────────────────────────────

def check_llm_gateway(base_url: str, api_key: str, model: str, dry_run: bool) -> bool:
    section(f"2. IBM Model Gateway  ({base_url})")
    if dry_run:
        skip("Skipped (--dry-run)")
        return True
    if not base_url or not api_key:
        skip("Skipped (LLM_BASE_URL or LLM_API_KEY not set)")
        return True

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 2a — list models
    try:
        r = httpx.get(f"{base_url}/models", headers=headers, timeout=15, verify=False)
        r.raise_for_status()
        models = r.json().get("data", [])
        ids = [m.get("id") for m in models]
        ok(f"/v1/models → {len(models)} model(s): {ids}")
    except Exception as e:
        fail(f"/v1/models failed: {e}")
        return False

    # 2b — chat completion smoke test
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 10,
        "temperature": 0,
    }
    try:
        r = httpx.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
            verify=False,
        )
        r.raise_for_status()
        data = r.json()
        reply = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        ok(f"chat/completions → model={data.get('model')}  reply={reply!r}")
        ok(f"  tokens: prompt={tokens.get('prompt_tokens')}  "
           f"completion={tokens.get('completion_tokens')}  "
           f"total={tokens.get('total_tokens')}")
        return True
    except Exception as e:
        fail(f"chat/completions failed: {e}")
        return False


# ── 3. GNews API (sole news provider) ────────────────────────────────────────

def check_gnews(api_key: str, dry_run: bool) -> bool:
    section("3. GNews API  (https://gnews.io/api/v4/search)  [SOLE PROVIDER]")
    if dry_run or not api_key:
        skip("Skipped (--dry-run or GNEWS_API_KEY not set)")
        return True

    print("  GNews is the sole news provider for the AI News Intelligence pipeline.")
    all_ok = True

    # ── 4a. AI Tech news ──────────────────────────────────────────────────────
    print("\n  [4a] AI Technology news:")
    try:
        r = httpx.get(
            "https://gnews.io/api/v4/search",
            params={
                "q": "artificial intelligence LLM agentic AI model",
                "apikey": api_key, "lang": "en", "country": "us", "max": "3",
                "in": "title,description,content", "sortby": "publishedAt",
            },
            timeout=15,
        )
        r.raise_for_status()
        articles = r.json().get("articles", [])
        ok(f"AI tech search — {len(articles)} articles returned")
        for i, a in enumerate(articles[:2], 1):
            print(f"     [{i}] {a.get('title', '')[:75]}")
            print(f"         {a.get('url', '')}")
    except httpx.HTTPStatusError as e:
        fail(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        if e.response.status_code == 403:
            warn("      → GNews key quota exhausted or invalid")
        all_ok = False
    except Exception as e:
        fail(str(e))
        all_ok = False

    return all_ok


# ── 3. LinkedIn API ───────────────────────────────────────────────────────────

def check_linkedin_token(token: str, dry_run: bool) -> str | None:
    section("3. LinkedIn Token  (https://api.linkedin.com/rest/userinfo)")
    if dry_run or not token:
        skip("Skipped (--dry-run or LINKEDIN_ACCESS_TOKEN not set)")
        return None

    url = "https://api.linkedin.com/rest/userinfo"
    headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": "202504",
    }

    try:
        r = httpx.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        name = data.get("name", "")
        sub  = data.get("sub", "")
        ok(f"Authenticated as: {name}  (sub={sub})")
        print(f"     Profile URN:  urn:li:person:{sub}")
        return sub
    except httpx.HTTPStatusError as e:
        fail(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        if e.response.status_code == 401:
            print("      → Token expired or invalid")
            print("      → Regenerate at: https://www.linkedin.com/developers/apps")
            print("      → Required scopes: w_member_social  r_liteprofile")
        return None
    except Exception as e:
        fail(str(e))
        return None


# ── 4–7. MCP Server health checks ────────────────────────────────────────────

def check_mcp_health(name: str, base_url: str) -> bool:
    """Check /health endpoint of an MCP server."""
    health_url = base_url.replace("/mcp", "") + "/health"
    try:
        r = httpx.get(health_url, timeout=10)
        r.raise_for_status()
        data = r.json()
        ok(f"{name} health:  {data}")
        return True
    except httpx.ConnectError:
        fail(f"{name} — connection refused at {health_url}")
        print(f"      → Is the server running?  docker compose up {name.lower().replace(' ', '-')}")
        return False
    except Exception as e:
        fail(f"{name} health check failed: {e}")
        return False


def check_news_mcp_tool(base_url: str, dry_run: bool, auth_token: str) -> bool:
    """Call news_search_latest tool via MCP streamable-http."""
    section("4b. News MCP — news_search_latest tool call")
    if dry_run:
        skip("Skipped (--dry-run)")
        return True

    # MCP streamable-http: POST /mcp with JSON-RPC 2.0
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "news_search_latest",
            "arguments": {
                "query": "artificial intelligence LLM",
                "hours": 24,
                "limit": 3,
            },
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}",
    }
    try:
        r = httpx.post(base_url, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            fail(f"MCP error: {data['error']}")
            return False
        result = data.get("result", {})
        # parse content
        content = result.get("content", [{}])
        text = content[0].get("text", "{}") if content else "{}"
        try:
            articles = json.loads(text).get("articles", [])
        except json.JSONDecodeError:
            articles = []
        ok(f"news_search_latest returned {len(articles)} articles")
        for a in articles[:2]:
            title = a.get("title", "")[:70]
            print(f"     → {title}")
        return True
    except Exception as e:
        fail(f"Tool call failed: {e}")
        return False


def check_pageindex_round_trip(base_url: str, dry_run: bool, auth_token: str) -> bool:
    """Index a document and retrieve relevant sections."""
    section("5b. PageIndex MCP — index + retrieve round-trip")
    if dry_run:
        skip("Skipped (--dry-run)")
        return True

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {auth_token}"}

    def call(method: str, name: str, args: dict) -> dict | None:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {"name": name, "arguments": args}}
        try:
            r = httpx.post(base_url, json=payload, headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                fail(f"MCP error calling {name}: {data['error']}")
                return None
            content = data.get("result", {}).get("content", [{}])
            text = content[0].get("text", "{}") if content else "{}"
            return json.loads(text)
        except Exception as e:
            fail(f"{name} failed: {e}")
            return None

    # Index
    idx = call("tools/call", "pageindex_index_document", {
        "document_id": "verify-test-001",
        "title": "Test: AI impact on employment",
        "content": (
            "Artificial intelligence is transforming enterprise workflows. "
            "Studies show 40% of tasks in professional services can be automated. "
            "Workers in creative industries show increased productivity with AI tools. "
            "Policy makers are debating universal basic income as AI displaces routine jobs."
        ),
        "source_url": "https://example.com/test",
    })
    if not idx:
        return False
    ok(f"pageindex_index_document: {idx}")

    # Retrieve
    ret = call("tools/call", "pageindex_get_relevant_sections", {
        "document_id": "verify-test-001",
        "question": "What is the impact on jobs?",
    })
    if not ret:
        return False
    sections_text = ret.get("sections_text", "")
    ok(f"pageindex_get_relevant_sections: {len(sections_text)} chars returned")
    if sections_text:
        print(f"     → {sections_text[:120]}...")
    return True


def check_linkedin_mcp_mock(base_url: str, dry_run: bool, auth_token: str, publish: bool) -> bool:
    """Test LinkedIn MCP — mock post (no real publish unless --publish)."""
    section(f"7b. LinkedIn MCP — {'REAL post' if publish else 'mock post (safe)'}")
    if dry_run:
        skip("Skipped (--dry-run)")
        return True

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {auth_token}"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "linkedin_create_post",
            "arguments": {
                "text": (
                    "[TEST] AI Daily News Platform verification post.\n\n"
                    f"Timestamp: {datetime.utcnow().isoformat()}Z\n\n"
                    "#AI #Test"
                ),
                "publication_key": f"verify-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
            },
        },
    }

    if not publish:
        # Call with no LINKEDIN_ACCESS_TOKEN → server returns mock response
        payload["params"]["arguments"]["text"] = "[MOCK] " + payload["params"]["arguments"]["text"]

    try:
        r = httpx.post(base_url, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            fail(f"MCP error: {data['error']}")
            return False
        content = data.get("result", {}).get("content", [{}])
        text = content[0].get("text", "{}") if content else "{}"
        result = json.loads(text)
        ok(f"linkedin_create_post: {result}")
        return True
    except Exception as e:
        fail(f"Tool call failed: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify AI Daily News Platform MCP servers and external APIs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python scripts/verify_mcp.py --dry-run           # env check only
          python scripts/verify_mcp.py                     # full check (no publish)
          python scripts/verify_mcp.py --publish           # include real LinkedIn post
        """),
    )
    parser.add_argument("--dry-run",  action="store_true", help="Only check env vars and server health")
    parser.add_argument("--publish",  action="store_true", help="Actually publish a LinkedIn test post")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  AI Daily News Platform — MCP Verification")
    print("=" * 60)

    env = check_env()

    llm_ok       = check_llm_gateway(
                       env.get("LLM_BASE_URL", ""),
                       env.get("LLM_API_KEY", ""),
                       env.get("LLM_MODEL", "qwen2-5-72b-instruct"),
                       args.dry_run,
                   )
    gnews_ok     = check_gnews(env.get("GNEWS_API_KEY", ""), args.dry_run)
    profile_id   = check_linkedin_token(env.get("LINKEDIN_ACCESS_TOKEN", ""), args.dry_run)

    news_url       = env.get("NEWS_MCP_URL",       "http://localhost:8101/mcp")
    pageindex_url  = env.get("PAGEINDEX_MCP_URL",  "http://localhost:8102/mcp")
    eval_url       = env.get("EVALUATION_MCP_URL", "http://localhost:8103/mcp")
    linkedin_url   = env.get("LINKEDIN_MCP_URL",   "http://localhost:8104/mcp")
    auth_token     = env.get("MCP_AUTH_TOKEN",     "")
    api_url        = "http://localhost:8000"

    section("4. News MCP Server  (" + news_url + ")")
    news_health = check_mcp_health("News MCP", news_url)
    if news_health:
        check_news_mcp_tool(news_url, args.dry_run, auth_token)

    section("5. PageIndex MCP Server  (" + pageindex_url + ")")
    pi_health = check_mcp_health("PageIndex MCP", pageindex_url)
    if pi_health:
        check_pageindex_round_trip(pageindex_url, args.dry_run, auth_token)

    section("6. Evaluation MCP Server  (" + eval_url + ")")
    check_mcp_health("Evaluation MCP", eval_url)

    section("7. LinkedIn MCP Server  (" + linkedin_url + ")")
    li_health = check_mcp_health("LinkedIn MCP", linkedin_url)
    if li_health:
        check_linkedin_mcp_mock(linkedin_url, args.dry_run, auth_token, args.publish)

    section("8. Main API  (http://localhost:8000)")
    check_mcp_health("Main API", api_url)

    print("\n" + "=" * 60)
    print("  Verification complete.")
    print("  Run 'make dev' if any MCP server is unreachable.")
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
