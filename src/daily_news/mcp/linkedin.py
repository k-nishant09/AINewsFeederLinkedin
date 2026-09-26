"""LinkedIn MCP client — direct httpx calls to linkedin_* tools.

Image upload flow (LinkedIn Asset API):
  1. linkedin_upload_image(image_path, description)
       → returns {"asset_urn": "urn:li:digitalmediaAsset:..."}
  2. linkedin_create_post_with_image(text, asset_urn, publication_key)
       → returns {"post_urn": "urn:li:share:...", "status": ...}

Publishing architecture (AIFeeders):
  1. create_post(text, publication_key)
       → returns {"post_urn": "urn:li:share:...", "id": ..., "status": ...}
  2. create_comment(post_urn, text, comment_key) × 5 personas
       → returns {"comment_urn": "urn:li:comment:...", "status": ...}
  3. create_comment_reply(post_urn, parent_comment_urn, text, reply_key) — optional nested replies

References:
  Posts API:    https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
  Comments API: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api
"""
from __future__ import annotations

from typing import Any

from daily_news.mcp.client import mcp_factory


class LinkedInMCPClient:

    # ── Profile / token ───────────────────────────────────────────────────────

    async def get_profile(self) -> dict[str, Any]:
        return await mcp_factory().linkedin.call("linkedin_get_profile", {})

    async def validate_token(self) -> dict[str, Any]:
        return await mcp_factory().linkedin.call("linkedin_validate_token", {})

    # ── Posts ─────────────────────────────────────────────────────────────────

    async def create_post(self, text: str, publication_key: str) -> dict[str, Any]:
        """
        Publish the main LinkedIn post.
        Returns {"post_urn": "urn:li:share:...", "id": str, "status": str}.
        post_urn must be stored and passed to create_comment for persona perspectives.
        """
        return await mcp_factory().linkedin.call(
            "linkedin_create_post",
            {"text": text, "publication_key": publication_key},
        )

    async def get_post(self, post_id: str) -> dict[str, Any]:
        return await mcp_factory().linkedin.call(
            "linkedin_get_post",
            {"post_id": post_id},
        )

    async def get_publish_status(self, post_id: str) -> dict[str, Any]:
        return await mcp_factory().linkedin.call(
            "linkedin_get_publish_status",
            {"post_id": post_id},
        )

    async def get_post_analytics(self, post_urn: str) -> dict[str, Any]:
        """
        Fetch engagement metrics (impressions, reactions, comments, reposts) for a published post.
        """
        try:
            return await mcp_factory().linkedin.call(
                "linkedin_get_post_analytics",
                {"post_urn": post_urn},
            )
        except Exception:
            return {
                "post_urn": post_urn,
                "impressions": 0,
                "reactions": 0,
                "comments": 0,
                "reposts": 0,
                "engagement_rate": 0.0,
            }

    # ── Comments ──────────────────────────────────────────────────────────────

    async def create_comment(
        self,
        post_urn: str,
        text: str,
        comment_key: str,
    ) -> dict[str, Any]:
        """
        Add a persona perspective as a comment on the post.
        post_urn  — returned by create_post (e.g. "urn:li:share:1234567890")
        comment_key — idempotency key, e.g. "{publication_key}:business"
        Returns {"comment_urn": "urn:li:comment:...", "status": str}.
        """
        return await mcp_factory().linkedin.call(
            "linkedin_create_comment",
            {"post_urn": post_urn, "text": text, "comment_key": comment_key},
        )

    async def get_comments(self, post_urn: str, count: int = 20) -> dict[str, Any]:
        return await mcp_factory().linkedin.call(
            "linkedin_get_comments",
            {"post_urn": post_urn, "count": count},
        )

    async def create_comment_reply(
        self,
        post_urn: str,
        parent_comment_urn: str,
        text: str,
        reply_key: str,
    ) -> dict[str, Any]:
        """
        Add a nested reply to an existing comment.
        parent_comment_urn — returned by create_comment (e.g. "urn:li:comment:...")
        """
        return await mcp_factory().linkedin.call(
            "linkedin_create_comment_reply",
            {
                "post_urn":            post_urn,
                "parent_comment_urn":  parent_comment_urn,
                "text":                text,
                "reply_key":           reply_key,
            },
        )

    # ── Image / media ─────────────────────────────────────────────────────────

    async def upload_image(
        self,
        image_path: str,
        description: str = "AIFeeders comic strip",
    ) -> dict[str, Any]:
        """
        Upload an image file to LinkedIn and return its asset URN.

        image_path  — local filesystem path to the PNG (or SVG fallback).
        Returns {"asset_urn": "urn:li:digitalmediaAsset:...", "status": str}.
        On error returns {"asset_urn": "", "status": "error", ...}.

        The LinkedIn MCP server reads the file from disk and calls:
          POST /v2/assets?action=registerUpload  (register)
          PUT  <upload_url>                       (binary upload)
        """
        try:
            return await mcp_factory().linkedin.call(
                "linkedin_upload_image",
                {"image_path": image_path, "description": description},
            )
        except Exception as exc:
            return {"asset_urn": "", "status": "error", "error": str(exc)}

    async def create_post_with_image(
        self,
        text: str,
        asset_urn: str,
        publication_key: str,
    ) -> dict[str, Any]:
        """
        Publish a LinkedIn post with an embedded image.

        text            — post body text
        asset_urn       — URN returned by upload_image()
        publication_key — idempotency key (same as create_post)

        Falls back to a text-only post if the asset_urn is empty.
        Returns {"post_urn": ..., "status": ..., "image_attached": bool}.
        """
        if not asset_urn:
            result = await self.create_post(text=text, publication_key=publication_key)
            return {**result, "image_attached": False}

        return await mcp_factory().linkedin.call(
            "linkedin_create_post_with_image",
            {
                "text":            text,
                "asset_urn":       asset_urn,
                "publication_key": publication_key,
            },
        )
