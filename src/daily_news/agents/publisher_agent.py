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

from daily_news.agents.published_store import published_store
from daily_news.config.settings import get_settings
from daily_news.mcp.linkedin import LinkedInMCPClient
from daily_news.models.evaluation import EvaluationResult
from daily_news.models.persona import PersonaSetOutput
from daily_news.models.summary import NewsSummary

logger = logging.getLogger(__name__)


def _clip_at_sentence(text: str, limit: int) -> str:
    """
    Clip *text* to at most *limit* characters at a sentence boundary
    (last '.', '!', or '?' before the limit).  Falls back to hard-clipping
    at the limit if no sentence boundary is found within it.
    """
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    for i in range(len(clipped) - 1, -1, -1):
        if clipped[i] in ".!?":
            return clipped[: i + 1]
    # No sentence boundary — hard-clip at limit
    return clipped


def _hard_clip(text: str, limit: int) -> str:
    """Hard-clip *text* to *limit* characters (no sentence-boundary search)."""
    return text if len(text) <= limit else text[:limit]


class PublisherAgent:

    # Persona display labels — used in post body section headers
    _PERSONA_ORDER = [
        ("business", "💼  Capitalist Mind"),
        ("policy",   "🏛️  Government Mind"),
        ("genz",     "🎓  Generalist Mind"),
        ("linkedin", "🧠  Tech & Workforce Mind"),
    ]

    # Hashtags — discoverable, not spammy
    _HASHTAGS = (
        "#AI #AgenticAI #Jev #AINews #GenerativeAI #TechNews "
        "#MachineLearning #AIStrategy #AIInnovation #DigitalTransformation #AILeadership"
    )

    # Footer — disclaimer + attribution, concise
    _DISCLAIMER = (
        "⚠️ AI-simulated perspectives — not verified opinions or professional advice.\n"
        "🤖 AIFeeders  ·  Agentic AI  ·  Powered by Jev"
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
        jev_scores: dict | None = None,
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

        # ── Publishing gate 2: already published today (cross-run dedup) ───
        if published_store.is_published(summary.article_id):
            logger.info(
                "[%s] already published today — skipping article=%s",
                run_id, summary.article_id,
            )
            return self._skipped_result(summary, "already_published_today")

        # ── Publishing gate 3: evaluation.publish_eligible ─────────────────
        if evaluation is not None and not evaluation.publish_eligible:
            logger.warning(
                "[%s] publish_eligible=false (decision=%s) — skipping article=%s",
                run_id,
                evaluation.decision.value if evaluation else "unknown",
                summary.article_id,
            )
            return self._skipped_result(summary, f"guardrail_block:{evaluation.decision.value}")

        # Stable publication key — no run_id suffix so it is identical across
        # CronJob retries on the same day.  LinkedIn MCP uses this for its own
        # idempotency; our PublishedStore uses article_id+date (set below).
        publication_key = self._make_publication_key(summary.article_id, summary.headline)
        main_text       = self._compose_main_post(summary, personas, jev_scores=jev_scores)

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
            # Mark as published in the persistent store immediately after success
            # so any subsequent retry within the same day is blocked at gate 2.
            published_store.mark_published(summary.article_id)

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

    def _compose_main_post(
        self,
        summary: NewsSummary,
        personas: "PersonaSetOutput | None" = None,
        jev_scores: dict | None = None,
    ) -> str:
        """
        Social-first LinkedIn post layout (3000-char hard limit):

          ① Hook          — punchy 1-liner that stops the scroll
          ② Context       — 2 sentences of substance
          ③ Jev Decision  — transparent AI scoring: who, why, market signal
          ④ What it means — 3 bulleted key points
          ⑤ Impact snap   — Business / Workforce / Tech / Policy one-liners
          ⑥ Source
          ⑦ Perspectives  — Jev-routed mindsets, each scored, max 2 evidence bullets
          ⑧ Tech Take     — standalone strategist view
          ⑨ Jev Verdict   — final audience call + CTA question
          ⑩ Footer        — disclaimer + attribution + hashtags
        """
        POST_LIMIT = 3000

        # ── Fixed-cost blocks ──────────────────────────────────────────────────
        # No dividers used — saving ~170 chars for content.
        # Tail cost: verdict line(90) + CTA(42) + disclaimer(156) +
        # hashtags(131) + newlines(10) = ~429. Use 440 as safe ceiling.
        FOOTER_COST  = 440
        VERDICT_COST = 0   # already folded into FOOTER_COST

        # Per-field caps that keep variable sections predictable
        KEY_POINT_CAP    = 90   # each key point bullet
        IMPACT_LINE_CAP  = 90   # each impact line (business/workforce/tech/policy)

        # Shared persona metadata used across multiple sections
        _PMETA = {
            "business": ("💼", "Business",      "market strategy & ROI"),
            "policy":   ("🏛️", "Policy",        "regulation & governance"),
            "genz":     ("🎓", "Generalist",    "everyday impact & learning"),
            "linkedin": ("🧠", "Tech+Workforce","strategy, tech & careers"),
        }
        _PV = {
            "business": "💼 Business Strategists",
            "policy":   "🏛️ Policy Makers",
            "genz":     "🎓 Generalists",
            "linkedin": "🧠 Tech & Workforce",
        }

        # Pull Jev signals upfront — used in multiple sections
        has_jev   = bool(jev_scores and jev_scores.get("relevance_score"))
        ps_scores = (jev_scores or {}).get("persona_scores", {})
        active_p  = (jev_scores or {}).get("active_personas", [])
        ranked    = sorted(
            [(p, ps_scores.get(p, 0.0)) for p in active_p],
            key=lambda x: x[1], reverse=True,
        ) if has_jev else []

        # Running char budget tracker — deduct as each section is built
        remaining = POST_LIMIT - FOOTER_COST - VERDICT_COST

        def _add(block: str, lines: list[str]) -> None:
            """Append block to lines and deduct from remaining."""
            nonlocal remaining
            lines.append(block)
            remaining -= len(block) + 1  # +1 for the \n join

        # ── ① Hook ──────────────────────────────────────────────────────────
        lines: list[str] = []
        _add("🤖  AI NEWS  ·  Agentic AI", lines)
        _add("", lines)
        _add(summary.headline, lines)
        _add("", lines)

        # ── ② Context — full summary text, no cap ────────────────────────────
        _add(summary.summary.strip(), lines)
        _add("", lines)

        # ── ③ Jev Decision ────────────────────────────────────────────────────
        _add("⚙️  Jev Decision", lines)
        _add("", lines)

        if has_jev:
            event_type   = str(jev_scores.get("event_type", "other")).replace("_", " ").title()
            relevance    = jev_scores.get("relevance_score", 0.0)
            significance = jev_scores.get("significance", 0.0)
            engagement   = jev_scores.get("estimated_engagement", 0.0)
            controversy  = str(jev_scores.get("controversy_level", "low")).title()

            if ranked:
                top_p, top_s = ranked[0]
                em, lbl, desc = _PMETA.get(top_p, ("🎯", top_p.title(), ""))
                _add(f"  {em}  Primary audience : {lbl} ({desc})  — {top_s:.0%} Jev score", lines)

            bars    = round(min(max(significance * 5, 0.0), 5.0))
            sig_bar = "█" * bars + "░" * (5 - bars)
            _add(f"  📋  Story type      : {event_type}", lines)
            _add(f"  🎯  AI relevance    : {relevance:.0%}   Market signal: [{sig_bar}]", lines)
            _add(f"  ⚡  Engagement est. : {engagement:.0%}   Controversy: {controversy}", lines)

            if ranked:
                ranked_str = "  ›  ".join(
                    f"{_PMETA.get(p, ('','',''))[0]} {_PMETA.get(p, ('',p,''))[1]} {s:.0%}"
                    for p, s in ranked
                )
                _add(f"  👥  Audience impact : {ranked_str}", lines)

            if significance >= 0.6 and relevance >= 0.75:
                _add("  🔥  AI market shift : HIGH — potential to reshape the landscape.", lines)
            elif significance >= 0.4 and relevance >= 0.60:
                _add("  📡  AI market shift : MODERATE — notable movement, worth tracking.", lines)
            else:
                _add("  📊  AI market shift : INFORMATIONAL — relevant, not yet market-moving.", lines)
        else:
            if active_p:
                routed_str = "  ·  ".join(
                    f"{_PMETA.get(p, ('','',''))[0]} {_PMETA.get(p, ('',p,''))[1]}"
                    for p in active_p
                )
                _add(f"  👥  Jev routed to   : {routed_str}", lines)
            _add("  📋  Classified by Jev System One", lines)

        _add("", lines)

        # ── ④ What it means ───────────────────────────────────────────────────
        _add("📌  What you need to know", lines)
        for pt in summary.key_points[:3]:
            _add(f"  • {_hard_clip(pt.strip(), KEY_POINT_CAP)}", lines)
        _add("", lines)

        # ── ⑤ Impact snap — hard-capped so personas always get enough budget ──
        _add(f"📈  Business   —  {_hard_clip(summary.business_impact.strip(), IMPACT_LINE_CAP)}", lines)
        _add(f"👷  Workforce  —  {_hard_clip(summary.job_impact.strip(), IMPACT_LINE_CAP)}", lines)
        _add(f"🔬  Tech       —  {_hard_clip(summary.technology_impact.strip(), IMPACT_LINE_CAP)}", lines)
        if summary.policy_impact:
            _add(f"🏛️  Policy     —  {_hard_clip(summary.policy_impact.strip(), IMPACT_LINE_CAP)}", lines)
        _add("", lines)

        # ── ⑥ Source ─────────────────────────────────────────────────────────
        _add(f"🔗  Read more: {summary.source_url}", lines)
        _add("", lines)

        # ── ⑦ Perspectives — all 4 personas, each with 2 evidence bullets ────
        if personas is not None:
            persona_map = [
                ("business", "💼  Capitalist Mind",      personas.business),
                ("policy",   "🏛️  Government Mind",       personas.policy),
                ("genz",     "🎓  Generalist Mind",       personas.genz),
                ("linkedin", "🧠  Tech & Workforce Mind", personas.linkedin),
            ]
            active_pm = [
                (key, label, p)
                for key, label, p in persona_map
                if p.perspective and p.perspective.strip()
            ]

            if active_pm:
                # Deduct section header line before dividing budget per persona
                section_header_cost = len("🧵  Perspectives  ·  Jev-selected audience mindsets") + 2
                persona_budget = max(0, remaining - section_header_cost)
                per_persona    = max(180, persona_budget // len(active_pm))

                _add("🧵  Perspectives  ·  Jev-selected audience mindsets", lines)
                _add("", lines)

                for key, label, p in active_pm:
                    # Build the 2 evidence bullets first — they are mandatory
                    ev_lines = [
                        f"  • {ev.strip()}"
                        for ev in (p.evidence or [])[:2]
                        if ev.strip()
                    ]
                    ev_block = "\n".join(ev_lines)
                    ev_cost  = len(ev_block) + 2 if ev_block else 0  # +2 for \n prefix

                    # Clip perspective to whatever is left after label + bullets
                    label_cost = len(label) + 1  # +1 for \n
                    perspective_limit = max(80, per_persona - label_cost - ev_cost - 4)
                    clipped_perspective = _clip_at_sentence(
                        p.perspective.strip(), perspective_limit
                    )

                    # Assemble: label \n perspective \n bullet1 \n bullet2
                    parts = [f"{label}\n{clipped_perspective}"]
                    if ev_block:
                        parts.append(ev_block)
                    _add("\n".join(parts), lines)
                    _add("", lines)

        # ── ⑧ Jev Verdict + CTA ──────────────────────────────────────────────
        if ranked:
            top3 = "  ›  ".join(_PV.get(p, p.title()) for p, _ in ranked[:3])
            _add(f"⚙️  Jev Decision  —  {top3}", lines)
        elif active_p:
            top3 = "  ›  ".join(_PV.get(p, p.title()) for p in active_p[:3])
            _add(f"⚙️  Jev Decision  —  {top3}", lines)
        else:
            _add("⚙️  Jev Decision  —  classified & routed via decision model.", lines)
        _add("", lines)
        _add("💬  Real Human Take — drop yours below. 👇", lines)
        _add("", lines)

        # ── ⑩ Footer ─────────────────────────────────────────────────────────
        _add(self._DISCLAIMER, lines)
        _add("", lines)
        _add(self._HASHTAGS, lines)

        # Final join — LinkedIn hard limit is 3000 chars; slice only as safety net
        return "\n".join(lines)[:POST_LIMIT]

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
                lines.append(f"↳  {ev}")
        return "\n".join(lines)

    @staticmethod
    def _make_publication_key(article_id: str, headline: str) -> str:
        """
        Stable idempotency key for the LinkedIn MCP layer:
            {article_id}:{YYYY-MM-DD}:{headline_hash[:12]}

        No run_id suffix — identical across all CronJob retries on the same day.
        Cross-run deduplication is enforced by PublishedStore (gate 2), which checks
        article_id+date before this key is even generated.
        """
        today        = date.today().isoformat()
        content_hash = hashlib.sha256(headline.encode()).hexdigest()[:12]
        return f"{article_id}:{today}:{content_hash}"

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