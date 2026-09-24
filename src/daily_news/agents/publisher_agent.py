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


def _linkedin_len(text: str) -> int:
    """
    Count text length as LinkedIn does: UTF-16 code units.
    Characters outside the Basic Multilingual Plane (U+FFFF+, e.g. most emoji)
    count as 2 UTF-16 units each. Python's len() counts them as 1.
    A post with 30 such emoji looks like 3000 chars in Python but is 3030
    UTF-16 units to LinkedIn — causing silent truncation mid-sentence.
    """
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)


def _clip_at_sentence(text: str, limit: int) -> str:
    """
    Clip *text* to at most *limit* LinkedIn UTF-16 units at a sentence boundary
    (last '.', '!', or '?' before the limit).  Appends '…' when clipped.
    Falls back to hard-clipping with '…' if no sentence boundary is found.
    """
    if _linkedin_len(text) <= limit:
        return text
    # Walk forward until we exceed the limit, then backtrack to sentence boundary
    units = 0
    cut = 0
    for i, c in enumerate(text):
        units += 2 if ord(c) > 0xFFFF else 1
        if units >= limit - 1:   # -1 to leave room for '…'
            cut = i
            break
    clipped = text[:cut]
    for i in range(len(clipped) - 1, -1, -1):
        if clipped[i] in ".!?":
            return clipped[: i + 1]
    return clipped + "…"


def _hard_clip(text: str, limit: int) -> str:
    """Hard-clip *text* to *limit* LinkedIn UTF-16 units (no sentence-boundary search)."""
    if _linkedin_len(text) <= limit:
        return text
    units = 0
    for i, c in enumerate(text):
        units += 2 if ord(c) > 0xFFFF else 1
        if units >= limit - 1:
            return text[:i] + "…"
    return text


class PublisherAgent:

    # Persona display labels — used in post body section headers
    _PERSONA_ORDER = [
        ("business", "💼  Capitalist Mind"),
        ("policy",   "🏛️  Government Mind"),
        ("genz",     "🎓  Generalist Mind"),
        ("linkedin", "🧠  Tech & Workforce Mind"),
    ]

    # Footer block — disclaimer + attribution + hashtags assembled as one unit.
    # Stored as a single string so _add() measures its true cost in one call
    # and it either fits entirely or is skipped entirely.  No hashtag-only truncation.
    _FOOTER = (
        "⚠️ AI-simulated perspectives — not verified opinions or professional advice.\n"
        "🤖 AIFeeders  ·  Agentic AI  ·  Powered by Jev\n\n"
        "#AI #AgenticAI #Jev #AINews #GenerativeAI #TechNews "
        "#MachineLearning #AIStrategy #AIInnovation #DigitalTransformation #AILeadership"
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
        main_text       = self._compose_main_post(summary, personas, jev_scores=jev_scores)
        # Key includes full post body hash — any content change forces a new LinkedIn post
        # instead of LinkedIn returning the old (possibly truncated) post's URN.
        publication_key = self._make_publication_key(summary.article_id, summary.headline, main_text)

        # Log both Python len and LinkedIn UTF-16 len for every post so truncation
        # issues are immediately visible in the logs without needing to reproduce.
        logger.info(
            "[%s] post composed article=%s python_len=%d linkedin_utf16_len=%d",
            run_id, summary.article_id, len(main_text), _linkedin_len(main_text),
        )
        # Log full post text so we can compare exactly what was sent vs what LinkedIn shows
        logger.info("[%s] POST TEXT START ---\n%s\n--- POST TEXT END", run_id, main_text)

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
        # LinkedIn counts characters as UTF-16 code units — emoji outside the BMP
        # (U+FFFF+) count as 2 units each. Use _linkedin_len() everywhere, not len().
        # Safety margin: 2900 instead of 3000 absorbs any remaining edge cases.
        POST_LIMIT = 2900

        # ── Fixed-cost budget constants ────────────────────────────────────────
        # The footer (_FOOTER + verdict line + CTA line + their separators) is
        # ALWAYS appended unconditionally at the end.  The body budget is sized
        # so body + footer never exceeds POST_LIMIT.
        #
        #   _FOOTER len (disclaimer + hashtags) = ~255
        #   verdict line (~95) + blank (1) + CTA (~44) + blank (1) = ~141
        #   separating \n between body and footer block       = 1
        #   ─────────────────────────────────────────────────────────
        #   Total footer cost                               ≈ 398
        #
        FOOTER_TEXT_COST = _linkedin_len(self._FOOTER) + 141 + 3   # recalculated at call time
        BODY_LIMIT  = POST_LIMIT - FOOTER_TEXT_COST
        # Summary cap: LLM can return 300–900 chars; cap at 350 so the Jev block
        # and all persona sections always have guaranteed budget.
        SUMMARY_CAP     = 350
        KEY_POINT_CAP   = 90    # per key-point bullet
        IMPACT_LINE_CAP = 120   # per impact snap line — raised from 88 to avoid mid-sentence clips

        # Shared persona display metadata
        _PMETA = {
            "business": ("💼", "Business",       "market strategy & ROI"),
            "policy":   ("🏛️",  "Policy",         "regulation & governance"),
            "genz":     ("🎓", "Generalist",     "everyday impact & learning"),
            "linkedin": ("🧠", "Tech+Workforce", "strategy, tech & careers"),
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
            [(p, ps_scores.get(p, 0.0)) for p in active_p if p in _PMETA],
            key=lambda x: x[1], reverse=True,
        ) if has_jev else []

        # ── Running char budget tracker ────────────────────────────────────────
        # _add() measures the true cost of a block including its trailing "\n"
        # added by "\n".join(lines).  The footer is appended unconditionally
        # after the body loop, so remaining tracks body chars only.
        remaining = BODY_LIMIT

        def _add(block: str, lines: list[str]) -> None:
            """Append block only if it fits.  Cost = _linkedin_len(block) + 1 for the join \\n."""
            nonlocal remaining
            cost = _linkedin_len(block) + 1
            if cost > remaining:
                return
            lines.append(block)
            remaining -= cost

        # Derive the hook category label from the Jev event_type when available
        if has_jev:
            _EVENT_LABEL = {
                "product_launch": "Product Launch",
                "funding":        "Funding & M&A",
                "regulation":     "AI Regulation",
                "research":       "AI Research",
                "acquisition":    "Funding & M&A",
                "other":          "AI News",
            }
            event_raw = str(jev_scores.get("event_type", "other")).lower().strip()
            hook_category = _EVENT_LABEL.get(event_raw, "AI News")
        else:
            hook_category = "AI News"

        # ── ① Hook ──────────────────────────────────────────────────────────
        lines: list[str] = []
        _add(f"🤖  {hook_category.upper()}  ·  Powered by Jev", lines)
        _add("", lines)
        _add(summary.headline, lines)
        _add("", lines)

        # ── ② Context — capped at SUMMARY_CAP chars at a sentence boundary ──
        summary_text = _clip_at_sentence(summary.summary.strip(), SUMMARY_CAP)
        _add(summary_text, lines)
        _add("", lines)

        # ── ③ Jev Decision block ──────────────────────────────────────────────
        if has_jev:
            event_type   = str(jev_scores.get("event_type", "other")).replace("_", " ").title()
            relevance    = jev_scores.get("relevance_score", 0.0)
            significance = jev_scores.get("significance", 0.0)
            engagement   = jev_scores.get("estimated_engagement", 0.0)
            controversy  = str(jev_scores.get("controversy_level", "low")).title()

            bars    = round(min(max(significance * 5, 0.0), 5.0))
            sig_bar = "█" * bars + "░" * (5 - bars)

            # Build the block as a single multi-line string so the budget cost is
            # counted exactly once, not once per sub-line.
            jev_lines: list[str] = ["⚙️  Jev Decision"]
            if ranked:
                top_p, top_s = ranked[0]
                em, lbl, desc = _PMETA[top_p]
                jev_lines.append(f"  {em}  Primary audience : {lbl} ({desc})  — {top_s:.0%} Jev score")
            jev_lines.append(f"  📋  Story type       : {event_type}")
            jev_lines.append(f"  🎯  AI relevance     : {relevance:.0%}   Market signal: [{sig_bar}]")
            jev_lines.append(f"  ⚡  Engagement est.  : {engagement:.0%}   Controversy: {controversy}")

            if ranked:
                # Limit to top-3 personas to keep the line short
                impact_parts = [
                    f"{_PMETA[p][0]} {_PMETA[p][1]} {s:.0%}"
                    for p, s in ranked[:3]
                ]
                jev_lines.append(f"  👥  Audience impact  : {'  ›  '.join(impact_parts)}")

            if significance >= 0.6 and relevance >= 0.75:
                jev_lines.append("  🔥  AI market shift  : HIGH — potential to reshape the landscape.")
            elif significance >= 0.4 and relevance >= 0.60:
                jev_lines.append("  📡  AI market shift  : MODERATE — notable movement, worth tracking.")
            else:
                jev_lines.append("  📊  AI market shift  : INFORMATIONAL — relevant, not yet market-moving.")

            _add("\n".join(jev_lines), lines)
        else:
            # No Jev scores — compact fallback
            jev_lines = ["⚙️  Jev Decision"]
            if active_p:
                routed = "  ·  ".join(
                    f"{_PMETA.get(p, ('','',''))[0]} {_PMETA.get(p, ('',p,''))[1]}"
                    for p in active_p
                )
                jev_lines.append(f"  👥  Routed to: {routed}")
            jev_lines.append("  📋  Classified by Jev System One")
            _add("\n".join(jev_lines), lines)

        _add("", lines)

        # ── ④ What you need to know ───────────────────────────────────────────
        _add("📌  What you need to know", lines)
        for pt in summary.key_points[:3]:
            _add(f"  • {_hard_clip(pt.strip(), KEY_POINT_CAP)}", lines)
        _add("", lines)

        # ── ⑤ Impact snap ─────────────────────────────────────────────────────
        _add(f"📈  Business   —  {_hard_clip(summary.business_impact.strip(), IMPACT_LINE_CAP)}", lines)
        _add(f"👷  Workforce  —  {_hard_clip(summary.job_impact.strip(), IMPACT_LINE_CAP)}", lines)
        _add(f"🔬  Tech       —  {_hard_clip(summary.technology_impact.strip(), IMPACT_LINE_CAP)}", lines)
        if summary.policy_impact and summary.policy_impact.strip():
            _add(f"🏛️  Policy     —  {_hard_clip(summary.policy_impact.strip(), IMPACT_LINE_CAP)}", lines)
        _add("", lines)

        # ── ⑥ Source ──────────────────────────────────────────────────────────
        _add(f"🔗  Read more: {summary.source_url}", lines)
        _add("", lines)

        # ── ⑦ Perspectives — Jev-routed personas, each with 2 evidence bullets ─
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
                n = len(active_pm)
                # Measure what the section header + its blank line will cost
                hdr      = "🧵  Perspectives  ·  Jev-selected audience mindsets"
                hdr_cost = len(hdr) + 1 + 1     # header \n + blank \n
                # Divide remaining budget (after header) equally across personas.
                # Each persona block also needs +1 for its trailing blank line.
                per_persona = max(200, (remaining - hdr_cost) // n) - 1

                _add(hdr, lines)
                _add("", lines)

                for key, label, p in active_pm:
                    label_cost = len(label) + 1      # label + \n
                    ev_budget  = per_persona - label_cost - 80   # leave ≥80 for perspective
                    # Cap each evidence bullet so total ev block fits in budget
                    ev_cap = max(60, ev_budget // 2) if ev_budget > 0 else 60
                    ev_lines = [
                        f"  • {_hard_clip(ev.strip(), ev_cap)}"
                        for ev in (p.evidence or [])[:2]
                        if ev.strip()
                    ]
                    ev_text  = "\n".join(ev_lines)
                    ev_cost  = len(ev_text) + 1 if ev_text else 0
                    persp_budget = max(60, per_persona - label_cost - ev_cost - 2)

                    clipped = _clip_at_sentence(p.perspective.strip(), persp_budget)

                    # Assemble into a single block so _add() counts cost once
                    block_parts = [label, clipped]
                    if ev_text:
                        block_parts.append(ev_text)
                    persona_block = "\n".join(block_parts)
                    _add(persona_block, lines)
                    _add("", lines)

        # ── ⑧ Verdict + CTA — appended unconditionally (part of footer budget) ─
        if ranked:
            verdict = "  ›  ".join(_PV.get(p, p.title()) for p, _ in ranked[:3])
        elif active_p:
            verdict = "  ›  ".join(_PV.get(p, p.title()) for p in active_p[:3])
        else:
            verdict = "classified & routed via Jev decision model"

        # ── ⑨ Footer — always present, appended after the body ────────────────
        # Assembled outside the budget loop so it is never skipped.
        footer_lines = [
            f"⚙️  Jev audience verdict  —  {verdict}",
            "",
            "💬  What's your take? Drop it below. 👇",
            "",
            self._FOOTER,
        ]
        footer_block = "\n".join(footer_lines)

        body = "\n".join(lines).rstrip()
        # Trim body if body + \n\n + footer exceeds POST_LIMIT (LinkedIn UTF-16 units)
        separator = "\n\n"
        max_body = POST_LIMIT - _linkedin_len(separator) - _linkedin_len(footer_block)
        if _linkedin_len(body) > max_body:
            body = _hard_clip(body, max_body)

        full_post = body + separator + footer_block
        # Final hard-safety guard — should never trigger given the budget math above
        if _linkedin_len(full_post) > POST_LIMIT:
            full_post = _hard_clip(full_post, POST_LIMIT)
        return full_post

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
    def _make_publication_key(article_id: str, headline: str, post_body: str = "") -> str:
        """
        Stable idempotency key for the LinkedIn MCP layer:
            {article_id}:{YYYY-MM-DD}:{body_hash[:12]}

        Includes a hash of the full post body (not just the headline) so any change
        to the composed post text — including a fix to how it is assembled — forces
        LinkedIn to create a NEW post rather than returning the old (possibly truncated)
        post's URN via its own server-side deduplication.

        No run_id suffix — identical across all CronJob retries on the same day
        as long as the composed body is identical.
        Cross-run deduplication is enforced by PublishedStore (gate 2).
        """
        today        = date.today().isoformat()
        # Hash the full composed post body so content changes force a new post
        content_hash = hashlib.sha256((post_body or headline).encode()).hexdigest()[:12]
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