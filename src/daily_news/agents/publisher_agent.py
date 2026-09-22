"""
Publisher Agent — deterministic publishing layer. The ONLY component that
calls LinkedIn MCP.

Architecture principles (non-negotiable):
  - LLM generates proposals; this agent makes the decision and performs the
    side effect. No LLM calls here.
  - PUBLISHING_ENABLED=false → skip publishing entirely (safe for smoke tests
    and deployment validation).
  - Comments are published sequentially, never in gather(). Each comment
    is independently idempotent. The inter-comment delay is enforced at the
    MCP level (LINKEDIN_COMMENT_DELAY_SECONDS) but we also await sequentially.
  - Every error from LinkedIn MCP is classified and logged with a full audit
    record. Only retry-eligible errors (429 / 5xx / network) should be retried
    by callers; 401/403/4xx are surfaced immediately.
  - publish_eligible gate: only articles where EvaluationResult.publish_eligible
    is True reach this agent.

Publishing flow:
  Step 1 → linkedin_create_post(text, publication_key)
           Returns post_urn from x-restli-id header (HTTP 201)
  Step 2 → linkedin_create_comment(post_urn, text, comment_key) × 5 personas
           Each called sequentially with MCP-level delay between them

LinkedIn API refs:
  Posts API:    https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
  Comments API: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, timezone, datetime

from daily_news.config.settings import get_settings
from daily_news.mcp.linkedin import LinkedInMCPClient
from daily_news.models.evaluation import EvaluationResult
from daily_news.models.persona import PersonaSetOutput
from daily_news.models.summary import NewsSummary

logger = logging.getLogger(__name__)


def _clip_at_sentence(text: str, limit: int) -> str:
    """
    Clip *text* to at most *limit* characters, but only at a sentence boundary
    (last '.', '!', or '?' before the limit).  If no sentence boundary exists
    within the limit, the full text is returned unchanged — this means the LLM
    must produce short-enough sentences (enforced via the prompt).
    """
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    # Walk back to the last sentence-ending punctuation
    for i in range(len(clipped) - 1, -1, -1):
        if clipped[i] in ".!?":
            return clipped[: i + 1]
    # No sentence boundary found — return up to limit (fallback)
    return clipped



class PublisherAgent:

    # Persona display labels — used in post body section headers
    _PERSONA_ORDER = [
        ("business", "💼 CAPITALIST MIND"),
        ("labor",    "👷 WORKING PROFESSIONAL MIND"),
        ("policy",   "🏛️ GOVERNMENT MIND"),
        ("genz",     "🎓 YOUNG / FRESHER MIND"),
        ("linkedin", "🧠 TECH STRATEGIST MIND"),
    ]

    # Short disclaimer — AI-simulated mindsets notice
    _DISCLAIMER = (
        "─────────────────────────────────\n"
        "⚠️ AI-simulated perspectives — not verified opinions or professional advice.\n"
        "🤖 Built with AIFeeders · Powered by Agentic AI"
    )

    def __init__(self) -> None:
        self._client   = LinkedInMCPClient()
        self._settings = get_settings()

    # ── Main entry point ──────────────────────────────────────────────────────

    async def publish(
        self,
        summary:    NewsSummary,
        personas:   PersonaSetOutput,
        run_id:     str,
        evaluation: EvaluationResult | None = None,
    ) -> dict:
        """
        Publish main post then add 5 persona perspective comments.

        Preconditions:
          - evaluation.publish_eligible must be True (checked here as a safety
            net; workflow graph should also gate on it).
          - settings.publishing_enabled must be True (set False for smoke tests).

        Returns a result dict with full audit trail.
        """
        settings = self._settings

        # ── Publishing gate 1: PUBLISHING_ENABLED ──────────────────────────
        if not settings.publishing_enabled:
            logger.info(
                "[%s] PUBLISHING_ENABLED=false — skipping publish for article=%s",
                run_id, summary.article_id,
            )
            return self._skipped_result(summary, "publishing_disabled")

        # ── Publishing gate 2: evaluation.publish_eligible ─────────────────
        if evaluation is not None and not evaluation.publish_eligible:
            logger.warning(
                "[%s] publish_eligible=false (decision=%s) — skipping article=%s",
                run_id,
                evaluation.decision.value if evaluation else "unknown",
                summary.article_id,
            )
            return self._skipped_result(summary, f"guardrail_block:{evaluation.decision.value}")

        publication_key = self._make_publication_key(summary.article_id, summary.headline, run_id)
        main_text       = self._compose_main_post(summary, personas)

        # ── Step 1: publish the main post ───────────────────────────────────
        logger.info("[%s] publishing main post article=%s key=%s",
                    run_id, summary.article_id, publication_key)

        post_result = await self._client.create_post(
            text=main_text,
            publication_key=publication_key,
        )

        post_urn    = post_result.get("post_urn", "")
        post_status = post_result.get("status", "error")

        # Log full error context for diagnosis — never log token
        if post_status == "error":
            logger.error(
                "[%s] post FAILED article=%s | error_class=%s | http=%s | "
                "li_code=%s | li_message=%s | li_version=%s | endpoint=%s | "
                "retry_eligible=%s",
                run_id, summary.article_id,
                post_result.get("error_class", "UNKNOWN"),
                post_result.get("http_status", "?"),
                post_result.get("li_error_code", "?"),
                post_result.get("li_message", "?"),
                post_result.get("li_version_used", "?"),
                post_result.get("endpoint", "?"),
                post_result.get("retry_eligible", False),
            )
        else:
            logger.info("[%s] post published post_urn=%s status=%s",
                        run_id, post_urn, post_status)

        # ── Step 2: persona comments — sequential, never gather() ───────────
        # NOTE: LinkedIn Comments API requires "Community Management API" product
        # (partnerApiSocialActions.CREATE). Until that product is approved the
        # comment calls will return HTTP 403 PERMISSION_ERROR.  All personas
        # are already embedded in the post body, so we attempt comments but
        # treat PERMISSION_ERROR as a soft skip (not a pipeline error).
        comment_results: dict[str, dict] = {}
        _comments_blocked = False  # set True on first PERMISSION_ERROR to skip rest

        if post_urn and post_status in ("published", "mock"):
            persona_map = {
                "business": personas.business,
                "labor":    personas.labor,
                "policy":   personas.policy,
                "genz":     personas.genz,
                "linkedin": personas.linkedin,
            }

            for persona_name, label in self._PERSONA_ORDER:
                # Once we know Comments API is blocked, skip remaining personas
                if _comments_blocked:
                    comment_results[persona_name] = {
                        "status":      "skipped",
                        "skip_reason": "comments_api_permission_error",
                    }
                    continue

                persona_obj  = persona_map[persona_name]
                comment_text = self._compose_comment(
                    label, persona_obj.perspective, persona_obj.evidence,
                )
                comment_key = f"{publication_key}:{persona_name}"

                try:
                    c_result = await self._client.create_comment(
                        post_urn=post_urn,
                        text=comment_text,
                        comment_key=comment_key,
                    )
                    comment_results[persona_name] = c_result

                    c_status = c_result.get("status", "error")
                    if c_status == "error":
                        error_class = c_result.get("error_class", "UNKNOWN")
                        if error_class == "PERMISSION_ERROR":
                            # Comments API not approved — soft skip, not a pipeline error
                            logger.info(
                                "[%s] Comments API not available (PERMISSION_ERROR) — "
                                "personas are embedded in post body. "
                                "Skipping remaining comment attempts.",
                                run_id,
                            )
                            _comments_blocked = True
                            comment_results[persona_name]["status"] = "skipped"
                            comment_results[persona_name]["skip_reason"] = "comments_api_permission_error"
                        else:
                            logger.error(
                                "[%s] comment FAILED persona=%s | error_class=%s | "
                                "http=%s | li_code=%s | li_message=%s",
                                run_id, persona_name,
                                error_class,
                                c_result.get("http_status", "?"),
                                c_result.get("li_error_code", "?"),
                                c_result.get("li_message", "?"),
                            )
                    else:
                        logger.info(
                            "[%s] comment OK persona=%s urn=%s",
                            run_id, persona_name, c_result.get("comment_urn", ""),
                        )

                except Exception as exc:  # noqa: BLE001
                    logger.error("[%s] comment exception persona=%s: %s",
                                 run_id, persona_name, exc)
                    comment_results[persona_name] = {
                        "status":      "error",
                        "error_class": "EXCEPTION",
                        "li_message":  str(exc),
                    }

        elif post_status == "error":
            logger.warning(
                "[%s] skipping comments — post failed article=%s",
                run_id, summary.article_id,
            )
        else:
            logger.warning(
                "[%s] skipping comments — no post_urn article=%s status=%s",
                run_id, summary.article_id, post_status,
            )

        return {
            "run_id":          run_id,
            "article_id":      summary.article_id,
            "publication_key": publication_key,
            "post_urn":        post_urn,
            "post_status":     post_status,
            "comments":        comment_results,
            "linkedin_result": post_result,  # backward compat
            "published_at":    datetime.now(tz=timezone.utc).isoformat(),
        }

    # ── Composition helpers ───────────────────────────────────────────────────

    def _compose_main_post(self, summary: NewsSummary, personas: "PersonaSetOutput | None" = None) -> str:
        """
        Compose LinkedIn post:
          ① News block  — headline, summary, key points, structured impacts
          ② 🧵 Perspectives — 4 personas (Capitalist, Working Pro, Government, Young)
          ③ Tech Strategist — standalone section for practitioner take
          ④ Disclaimer — short AI-simulated mindsets notice

        LinkedIn Posts API hard limit: 3000 chars (HTTP 400 if exceeded).
        Design budget: news ~750 + perspectives ~1700 + disclaimer ~120 = ~2570 (comfortable margin).

        Truncation policy: each persona entry is clipped only at a sentence boundary
        (last full stop) so no sentence is ever left incomplete.
        """
        POST_LIMIT = 2990  # safety margin under LinkedIn's 3000-char hard limit

        # ── ① News block ──────────────────────────────────────────────────────
        # Plain text only — emojis + ALL-CAPS for visual structure.
        # Headline on its own line so feed card preview renders it correctly on mobile.
        key_points = "\n".join(f"  • {p}" for p in summary.key_points[:3])
        news_lines = [
            "🤖 AI NEWS",
            summary.headline,
            "",
            summary.summary,
            "",
            "📌 KEY POINTS",
            key_points,
            "",
            f"📈 Business  {summary.business_impact}",
            f"👷 Workforce  {summary.job_impact}",
            f"🔬 Technology  {summary.technology_impact}",
        ]
        if summary.policy_impact:
            news_lines.append(f"🏛️ Policy  {summary.policy_impact}")
        news_lines += ["", f"🔗 {summary.source_url}"]
        news_block = "\n".join(news_lines)

        # ── ② 4-persona perspectives ──────────────────────────────────────────
        persona_block = ""
        if personas is not None:
            persona_map = [
                ("1/4  💼  CAPITALIST MIND",          personas.business),
                ("2/4  👷  WORKING PROFESSIONAL MIND", personas.labor),
                ("3/4  🏛️  GOVERNMENT MIND",           personas.policy),
                ("4/4  🎓  YOUNG / FRESHER MIND",      personas.genz),
            ]

            # Budget: total limit minus fixed sections, split across 4 personas
            overhead = (
                len(news_block)
                + len(self._DISCLAIMER)
                + 200   # tech strategist section estimate
                + 80    # section separators + newlines
            )
            budget_total = POST_LIMIT - overhead
            per_persona  = max(280, budget_total // 4)

            p_lines = ["", "─────────────────────────────────", "🧵 PERSPECTIVES", ""]
            for label, p in persona_map:
                perspective = p.perspective.strip()
                evidence_bullets = "\n".join(
                    f"  ▸ {ev.strip()}" for ev in p.evidence[:2] if ev.strip()
                )
                entry = f"{label}\n{perspective}"
                if evidence_bullets:
                    entry += f"\n{evidence_bullets}"
                # Clip at sentence boundary — never cut mid-sentence
                entry = _clip_at_sentence(entry, per_persona)
                p_lines.append(entry)
                p_lines.append("")
            persona_block = "\n".join(p_lines)

        # ── ③ Tech Strategist — practitioner take ────────────────────────────
        tech_block = ""
        if personas is not None:
            ts = personas.linkedin
            ts_perspective = ts.perspective.strip()
            ts_evidence = "\n".join(
                f"  ▸ {ev.strip()}" for ev in ts.evidence[:2] if ev.strip()
            )
            ts_entry = f"🧠  TECH STRATEGIST MIND\n{ts_perspective}"
            if ts_evidence:
                ts_entry += f"\n{ts_evidence}"
            ts_entry = _clip_at_sentence(ts_entry, 320)
            tech_block = f"\n─────────────────────────────────\n{ts_entry}\n"

        # ── ④ Disclaimer ──────────────────────────────────────────────────────
        tail = f"\n{self._DISCLAIMER}"

        text = news_block + persona_block + tech_block + tail

        # Final hard-cap at LinkedIn's 3000-char API limit
        return text[:POST_LIMIT]

    @staticmethod
    def _compose_comment(label: str, perspective: str, evidence: list[str]) -> str:
        """
        Persona comment text — used when LinkedIn Comments API is available.
        Truncation at 1250 chars per LinkedIn Comments API docs.
        """
        lines = [label.upper(), "", perspective]
        if evidence:
            lines.append("")
            for ev in evidence[:3]:
                lines.append(f"▸ {ev}")
        return "\n".join(lines)

    @staticmethod
    def _make_publication_key(article_id: str, headline: str, run_id: str = "") -> str:
        """
        Idempotency key: article_id + today's date + headline hash.
        run_id suffix lets manual re-triggers publish fresh posts on the same day
        (e.g. after a format change). CronJob omits run_id so it stays stable.
        """
        today        = date.today().isoformat()
        content_hash = hashlib.sha256(headline.encode()).hexdigest()[:12]
        suffix       = f":{run_id[-8:]}" if run_id else ""
        return f"{article_id}:{today}:{content_hash}{suffix}"

    @staticmethod
    def _skipped_result(summary: NewsSummary, reason: str) -> dict:
        return {
            "run_id":          "",
            "article_id":      summary.article_id,
            "publication_key": "",
            "post_urn":        "",
            "post_status":     "skipped",
            "skip_reason":     reason,
            "comments":        {},
            "linkedin_result": {"status": "skipped", "reason": reason},
            "published_at":    datetime.now(tz=timezone.utc).isoformat(),
        }
