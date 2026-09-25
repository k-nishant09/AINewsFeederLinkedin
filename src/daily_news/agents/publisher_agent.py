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
  Step 2 → linkedin_create_comment(post_urn, text, comment_key) × 4 personas
           Each called sequentially with MCP-level delay between them

LinkedIn API refs:
  Posts API:    https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
  Comments API: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/comments-api
"""
from __future__ import annotations

import hashlib
import logging
import re
import string
from datetime import date, timezone, datetime

from daily_news.agents.published_store import published_store
from daily_news.config.settings import get_settings
from daily_news.mcp.linkedin import LinkedInMCPClient
from daily_news.models.evaluation import EvaluationResult
from daily_news.models.persona import PersonaSetOutput
from daily_news.models.summary import NewsSummary

logger = logging.getLogger(__name__)


# ── LinkedIn character counting ───────────────────────────────────────────────

def _linkedin_len(text: str) -> int:
    """
    Count text length as LinkedIn does: UTF-16 code units.
    Characters outside the Basic Multilingual Plane (U+FFFF+, e.g. most emoji)
    count as 2 UTF-16 units each. Python's len() counts them as 1.
    """
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)


def _clip_at_sentence(text: str, limit: int) -> str:
    """
    Clip *text* to at most *limit* LinkedIn UTF-16 units at the last sentence
    boundary ('.', '!', '?') before the limit. Appends '…' when clipped.
    Falls back to hard-clip with '…' if no sentence boundary is found.
    """
    if _linkedin_len(text) <= limit:
        return text
    units = 0
    cut = 0
    for i, c in enumerate(text):
        units += 2 if ord(c) > 0xFFFF else 1
        if units >= limit - 1:
            cut = i
            break
    clipped = text[:cut]
    for i in range(len(clipped) - 1, -1, -1):
        if clipped[i] in ".!?":
            return clipped[: i + 1]
    return clipped + "…"


def _hard_clip(text: str, limit: int) -> str:
    """Hard-clip *text* to *limit* LinkedIn UTF-16 units."""
    if _linkedin_len(text) <= limit:
        return text
    units = 0
    for i, c in enumerate(text):
        units += 2 if ord(c) > 0xFFFF else 1
        if units >= limit - 1:
            return text[:i] + "…"
    return text


# ── Dynamic hashtag extraction ────────────────────────────────────────────────

# Well-known AI company / product names → their canonical hashtag form.
# Derived at runtime from article text — not hardcoded into any post.
_KNOWN_ENTITIES: dict[str, str] = {
    "openai":       "OpenAI",
    "anthropic":    "Anthropic",
    "google":       "Google",
    "deepmind":     "DeepMind",
    "microsoft":    "Microsoft",
    "meta":         "MetaAI",
    "nvidia":       "NVIDIA",
    "amazon":       "Amazon",
    "aws":          "AWS",
    "ibm":          "IBM",
    "apple":        "Apple",
    "mistral":      "Mistral",
    "cohere":       "Cohere",
    "stability":    "StabilityAI",
    "hugging face": "HuggingFace",
    "huggingface":  "HuggingFace",
    "salesforce":   "Salesforce",
    "palantir":     "Palantir",
    "databricks":   "Databricks",
    "groq":         "Groq",
    "perplexity":   "Perplexity",
    "midjourney":   "Midjourney",
    "runway":       "RunwayML",
    "eu ai act":    "EUAIAct",
    "eu":           None,          # too generic — skip
    "llm":          "LLM",
    "gpt":          "GPT",
    "gemini":       "Gemini",
    "claude":       "Claude",
    "llama":        "Llama",
    "chatgpt":      "ChatGPT",
}

# Topic → hashtag mapping derived from Jev event_type / category signals
_EVENT_HASHTAGS: dict[str, list[str]] = {
    "product_launch": ["AIProductLaunch", "ProductLaunch"],
    "funding":        ["AIFunding", "VentureCapital"],
    "regulation":     ["AIRegulation", "AIGovernance", "AIPolicy"],
    "research":       ["AIResearch", "MLResearch"],
    "acquisition":    ["MergersAndAcquisitions", "AIFunding"],
    "other":          [],
}


def _extract_dynamic_tags(
    headline: str,
    summary: str,
    source: str,
    key_points: list[str],
    event_type: str,
    max_tags: int = 6,
) -> list[str]:
    """
    Extract up to *max_tags* hashtags dynamically from article content.

    Priority order:
      1. Company/product names found in the article text
      2. Event-type topic tags from Jev classification
      3. Nothing else — quality over quantity

    Returns a list of #Tag strings ready to append to the footer.
    No duplicates, no duplicates of the base brand tags.
    """
    corpus = " ".join(
        [headline, summary, source] + key_points
    ).lower()

    found_tags: list[str] = []
    seen_lower: set[str] = set()

    # Pass 1 — match known entities in the corpus
    for pattern, tag in _KNOWN_ENTITIES.items():
        if tag is None:
            continue
        if re.search(r"\b" + re.escape(pattern) + r"\b", corpus):
            tag_lower = tag.lower()
            if tag_lower not in seen_lower:
                seen_lower.add(tag_lower)
                found_tags.append(f"#{tag}")
            if len(found_tags) >= max_tags:
                break

    # Pass 2 — fill remaining slots with event-type topic tags
    remaining = max_tags - len(found_tags)
    if remaining > 0:
        for tag in _EVENT_HASHTAGS.get(event_type, []):
            tag_lower = tag.lower()
            if tag_lower not in seen_lower:
                seen_lower.add(tag_lower)
                found_tags.append(f"#{tag}")
                remaining -= 1
                if remaining <= 0:
                    break

    return found_tags


# ── Dynamic CTA generator ─────────────────────────────────────────────────────

def _build_cta(
    headline: str,
    event_type: str,
    controversy: str,
    top_persona: str,
    source: str,
) -> str:
    """
    Build a specific, article-grounded call-to-action question.

    Rules:
      - Derived entirely from article signals — no hardcoded question strings.
      - Uses headline keywords + event_type + controversy to select the tension.
      - Ends with a direct invite to comment — makes commenting feel natural.
    """
    headline_lower = headline.lower()
    controversy_lower = controversy.lower()
    is_controversial = controversy_lower in ("high", "medium")

    # Extract a short subject phrase from the headline for specificity.
    # Strip common filler words and take the first meaningful noun cluster.
    stop = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
        "of", "for", "with", "by", "from", "as", "is", "are", "was",
        "were", "be", "been", "will", "that", "this", "it", "its",
        "still", "new", "latest", "ai", "models", "model",
    }
    words = [
        w.strip(string.punctuation)
        for w in headline.split()
        if w.strip(string.punctuation).lower() not in stop and len(w) > 2
    ]
    subject = " ".join(words[:4]) if words else "this development"

    if event_type == "regulation":
        if is_controversial:
            return (
                f"Should companies be legally required to publish {subject} results publicly? "
                f"Yes / No — and why? Comment below. 👇"
            )
        return (
            f"What regulation around {subject} would actually make AI safer — not just compliant? "
            f"Practitioners and policy folks — share your view. 👇"
        )

    if event_type == "product_launch":
        return (
            f"Will {subject} change how your team works — or is it another tool you'll ignore in 6 months? "
            f"Honest takes only. 👇"
        )

    if event_type in ("funding", "acquisition"):
        return (
            f"Does consolidation around {subject} make AI better for everyone — or just bigger players? "
            f"Where do you stand? 👇"
        )

    if event_type == "research":
        return (
            f"Does research like {subject} change anything you do today — or does it take years to matter? "
            f"Drop your read below. 👇"
        )

    # Fallback — persona-driven question
    if top_persona == "business":
        return (
            f"Is your org already factoring {subject} into your AI strategy? "
            f"What's the business case you're making internally? 👇"
        )
    if top_persona == "policy":
        return (
            f"What guardrails should exist around {subject}? "
            f"Regulators, builders, and users — all angles welcome. 👇"
        )
    if top_persona == "linkedin":
        return (
            f"What does {subject} mean for your role or team in the next 12 months? "
            f"Practitioners — tell us what you're seeing on the ground. 👇"
        )
    # genz / other
    return (
        f"What's the most overlooked implication of {subject}? "
        f"Share the take others aren't saying. 👇"
    )


# ── Signal block builder ──────────────────────────────────────────────────────

def _build_signal_block(
    relevance: float,
    significance: float,
    engagement: float,
    controversy: str,
    ranked: list[tuple[str, float]],
    pmeta: dict,
) -> str:
    """
    Build the 'Why this matters' data signal block.
    Framed for the reader — no 'Jev' branding, no 'Story type', no AI jargon.
    All values are derived from Jev scores passed in; nothing is hardcoded.
    """
    bars     = round(min(max(significance * 5, 0.0), 5.0))
    sig_bar  = "█" * bars + "░" * (5 - bars)

    # Momentum label — plain English, driven entirely by score thresholds
    if significance >= 0.6 and relevance >= 0.75:
        momentum = "🔥 High — reshaping the AI landscape"
    elif significance >= 0.4 and relevance >= 0.60:
        momentum = "📈 Moderate — worth tracking closely"
    else:
        momentum = "📊 Informational — relevant, early stage"

    # Audience breadcrumb — show all scored personas as chips (none hardcoded)
    if ranked:
        audience = "  ·  ".join(
            f"{pmeta[p][0]} {pmeta[p][1]} {s:.0%}"
            for p, s in ranked
            if p in pmeta
        )
    else:
        audience = "Broad AI audience"

    signal_lines = [
        "🔍  Why this matters",
        f"  📡  Signal strength  : {relevance:.0%} relevance   [{sig_bar}] significance",
        f"  ⚡  Engagement pulse : {engagement:.0%}   Controversy: {controversy}",
        f"  👥  Relevant to      : {audience}",
        f"  {momentum}",
    ]
    return "\n".join(signal_lines)


# ── Main Publisher Agent ──────────────────────────────────────────────────────

class PublisherAgent:

    # Persona display order and labels — fixed structure, names are the brand identity
    _PERSONA_ORDER = [
        ("business", "💼  Capitalist Mind"),
        ("policy",   "🏛️  Government Mind"),
        ("genz",     "🎓  Generalist Mind"),
        ("linkedin", "🧠  Tech & Workforce Mind"),
    ]

    # Base brand footer — only AIFeeders is hardcoded (it is the app identity).
    # Dynamic hashtags are appended at compose time from article content.
    _FOOTER_BASE = (
        "⚠️ Perspectives are AI-simulated — not professional advice.\n"
        "🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev"
    )

    # Core brand hashtags always present — topic + company tags added dynamically
    _BASE_HASHTAGS = (
        "#AI #AINews #GenerativeAI #MachineLearning "
        "#AIStrategy #AIInnovation #DigitalTransformation"
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
        Publish main post then attempt persona comments.

        Preconditions:
          - evaluation.publish_eligible must be True.
          - settings.publishing_enabled must be True.

        Returns a result dict with full audit trail.
        """
        settings = self._settings

        if not settings.publishing_enabled:
            logger.info("[%s] PUBLISHING_ENABLED=false — skipping article=%s", run_id, summary.article_id)
            return self._skipped_result(summary, "publishing_disabled")

        if published_store.is_published(summary.article_id):
            logger.info("[%s] already published today — skipping article=%s", run_id, summary.article_id)
            return self._skipped_result(summary, "already_published_today")

        if evaluation is not None and not evaluation.publish_eligible:
            logger.warning(
                "[%s] publish_eligible=false (decision=%s) — skipping article=%s",
                run_id, evaluation.decision.value if evaluation else "unknown", summary.article_id,
            )
            return self._skipped_result(summary, f"guardrail_block:{evaluation.decision.value}")

        main_text       = self._compose_main_post(summary, personas, jev_scores=jev_scores)
        publication_key = self._make_publication_key(summary.article_id, summary.headline, main_text)

        logger.info(
            "[%s] post composed article=%s python_len=%d linkedin_utf16_len=%d",
            run_id, summary.article_id, len(main_text), _linkedin_len(main_text),
        )
        logger.info("[%s] POST TEXT START ---\n%s\n--- POST TEXT END", run_id, main_text)
        logger.info("[%s] publishing main post article=%s key=%s", run_id, summary.article_id, publication_key)

        post_result = await self._client.create_post(text=main_text, publication_key=publication_key)
        post_urn    = post_result.get("post_urn", "")
        post_status = post_result.get("status", "error")

        if post_status == "error":
            logger.error(
                "[%s] post FAILED article=%s | error_class=%s | http=%s | "
                "li_code=%s | li_message=%s | li_version=%s | endpoint=%s | retry_eligible=%s",
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
            logger.info("[%s] post published post_urn=%s status=%s", run_id, post_urn, post_status)
            published_store.mark_published(summary.article_id)

        # ── Persona comments — sequential, never gather() ─────────────────────
        # Comments API requires "Community Management API" product approval.
        # Until approved, PERMISSION_ERROR is a soft skip — all personas are
        # already embedded in the post body.
        comment_results: dict[str, dict] = {}
        _comments_blocked = False

        if post_urn and post_status in ("published", "mock"):
            persona_map = {
                "business": personas.business,
                "policy":   personas.policy,
                "genz":     personas.genz,
                "linkedin": personas.linkedin,
            }

            for persona_name, label in self._PERSONA_ORDER:
                if _comments_blocked:
                    comment_results[persona_name] = {"status": "skipped", "skip_reason": "comments_api_permission_error"}
                    continue

                persona_obj  = persona_map[persona_name]
                comment_text = self._compose_comment(label, persona_obj.perspective, persona_obj.evidence)
                comment_key  = f"{publication_key}:{persona_name}"

                try:
                    c_result = await self._client.create_comment(post_urn=post_urn, text=comment_text, comment_key=comment_key)
                    comment_results[persona_name] = c_result
                    c_status = c_result.get("status", "error")

                    if c_status == "error":
                        error_class = c_result.get("error_class", "UNKNOWN")
                        if error_class == "PERMISSION_ERROR":
                            logger.info(
                                "[%s] Comments API not available (PERMISSION_ERROR) — "
                                "personas embedded in post body. Skipping remaining.",
                                run_id,
                            )
                            _comments_blocked = True
                            comment_results[persona_name]["status"]      = "skipped"
                            comment_results[persona_name]["skip_reason"] = "comments_api_permission_error"
                        else:
                            logger.error(
                                "[%s] comment FAILED persona=%s | error_class=%s | http=%s | li_code=%s | li_message=%s",
                                run_id, persona_name, error_class,
                                c_result.get("http_status", "?"),
                                c_result.get("li_error_code", "?"),
                                c_result.get("li_message", "?"),
                            )
                    else:
                        logger.info("[%s] comment OK persona=%s urn=%s", run_id, persona_name, c_result.get("comment_urn", ""))

                except Exception as exc:  # noqa: BLE001
                    logger.error("[%s] comment exception persona=%s: %s", run_id, persona_name, exc)
                    comment_results[persona_name] = {"status": "error", "error_class": "EXCEPTION", "li_message": str(exc)}

        elif post_status == "error":
            logger.warning("[%s] skipping comments — post failed article=%s", run_id, summary.article_id)
        else:
            logger.warning("[%s] skipping comments — no post_urn article=%s status=%s", run_id, summary.article_id, post_status)

        return {
            "run_id":          run_id,
            "article_id":      summary.article_id,
            "publication_key": publication_key,
            "post_urn":        post_urn,
            "post_status":     post_status,
            "comments":        comment_results,
            "linkedin_result": post_result,
            "published_at":    datetime.now(tz=timezone.utc).isoformat(),
        }

    # ── Post composition ──────────────────────────────────────────────────────

    def _compose_main_post(
        self,
        summary: NewsSummary,
        personas: "PersonaSetOutput | None" = None,
        jev_scores: dict | None = None,
    ) -> str:
        """
        LinkedIn Marketing Expert post layout — humanised, engagement-first.
        3000-char hard limit (2900 safety margin). Everything is data-driven.

        Flow:
          ① Category pill  — event label + source company name, no hardcoding
          ② Headline       — the news as a bold statement
          ③ Opening hook   — why_it_matters field: the human "so what"
          ④ Signal strip   — 3 data-driven lines (no 'Jev' branding shown)
          ⑤ 3 sharp facts  — specific, full-sentence key points
          ⑥ Impact quad    — Business / Workforce / Tech / Policy
          ⑦ Source link
          ⑧ 4 Voices       — all 4 mindsets, header titled from story angle
          ⑨ CTA question   — specific, article-grounded, drives comments
          ⑩ Footer         — base disclaimer + dynamic hashtags
        """
        POST_LIMIT   = 2900
        SUMMARY_CAP  = 240   # opening hook — tight and punchy
        KP_CAP       = 160   # per key-point bullet — raised so full sentences always fit
        IMPACT_CAP   = 160   # per impact line — raised so full sentences always fit
        PERSP_MIN    = 220   # min budget for one complete ≤200-char persona sentence
        BLANK_COST   = 1

        # ── Persona metadata — labels drive section headers and audience chips ──
        _PMETA: dict[str, tuple[str, str, str]] = {
            "business": ("💼", "Business",       "ROI & market strategy"),
            "policy":   ("🏛️",  "Policy",         "regulation & governance"),
            "genz":     ("🎓", "Generalist",     "everyday & societal impact"),
            "linkedin": ("🧠", "Tech+Workforce", "engineering & careers"),
        }

        # ── Extract Jev signals ────────────────────────────────────────────────
        has_jev      = bool(jev_scores and jev_scores.get("relevance_score"))
        ps_scores    = (jev_scores or {}).get("persona_scores", {})
        active_p     = (jev_scores or {}).get("active_personas", [])
        event_type   = str((jev_scores or {}).get("event_type", "other")).lower().strip()
        relevance    = float((jev_scores or {}).get("relevance_score",    0.0))
        significance = float((jev_scores or {}).get("significance",       0.0))
        engagement   = float((jev_scores or {}).get("estimated_engagement", 0.0))
        controversy  = str((jev_scores or {}).get("controversy_level", "low")).title()

        # All active personas sorted by score descending
        ranked: list[tuple[str, float]] = sorted(
            [(p, ps_scores.get(p, 0.0)) for p in active_p if p in _PMETA],
            key=lambda x: x[1], reverse=True,
        ) if has_jev else []

        top_persona = ranked[0][0] if ranked else (active_p[0] if active_p else "linkedin")

        # ── Budget tracker ─────────────────────────────────────────────────────
        remaining = POST_LIMIT  # footer is subtracted at assembly, not here

        def _add(block: str, lines: list[str]) -> None:
            nonlocal remaining
            cost = _linkedin_len(block) + 1
            if cost > remaining:
                return
            lines.append(block)
            remaining -= cost

        # ── ① Category pill ───────────────────────────────────────────────────
        # Event label derived from Jev event_type — nothing hardcoded.
        # Source company name injected live from summary.source.
        _EVENT_EMOJI: dict[str, str] = {
            "product_launch": "🚀",
            "funding":        "💰",
            "regulation":     "🏛️",
            "research":       "🔬",
            "acquisition":    "🤝",
            "other":          "📡",
        }
        _EVENT_LABEL: dict[str, str] = {
            "product_launch": "Product Launch",
            "funding":        "Funding & M&A",
            "regulation":     "AI Regulation",
            "research":       "AI Research",
            "acquisition":    "Acquisition",
            "other":          "AI Intelligence",
        }
        evt_emoji = _EVENT_EMOJI.get(event_type, "📡")
        evt_label = _EVENT_LABEL.get(event_type, "AI Intelligence")
        src_name  = summary.source.strip() if summary.source else ""
        pill      = f"{evt_emoji}  {evt_label}  ·  {src_name}  ·  AIFeeders" if src_name else f"{evt_emoji}  {evt_label}  ·  AIFeeders"

        lines: list[str] = []
        _add(pill, lines)
        _add("", lines)

        # ── ② Headline ────────────────────────────────────────────────────────
        _add(summary.headline, lines)
        _add("", lines)

        # ── ③ Opening hook — why_it_matters, capped for punch ─────────────────
        hook = _clip_at_sentence(summary.why_it_matters.strip(), SUMMARY_CAP)
        _add(hook, lines)
        _add("", lines)

        # ── ④ Signal strip — data-driven, reader-framed, no AI jargon ─────────
        if has_jev:
            signal_block = _build_signal_block(
                relevance, significance, engagement, controversy, ranked, _PMETA,
            )
            _add(signal_block, lines)
            _add("", lines)

        # ── ⑤ 3 sharp facts ───────────────────────────────────────────────────
        # _clip_at_sentence used so any text that exceeds the cap is trimmed
        # at the last full stop — never mid-word or mid-sentence.
        _add("📌  3 things to know", lines)
        for pt in summary.key_points[:3]:
            _add(f"  • {_clip_at_sentence(pt.strip(), KP_CAP)}", lines)
        _add("", lines)

        # ── ⑥ Impact quad ─────────────────────────────────────────────────────
        # _clip_at_sentence ensures each impact line ends at a sentence boundary.
        _add(f"📈  Business   —  {_clip_at_sentence(summary.business_impact.strip(),   IMPACT_CAP)}", lines)
        _add(f"👷  Workforce  —  {_clip_at_sentence(summary.job_impact.strip(),        IMPACT_CAP)}", lines)
        _add(f"🔬  Tech       —  {_clip_at_sentence(summary.technology_impact.strip(), IMPACT_CAP)}", lines)
        if summary.policy_impact and summary.policy_impact.strip():
            _add(f"🏛️  Policy     —  {_clip_at_sentence(summary.policy_impact.strip(), IMPACT_CAP)}", lines)
        _add("", lines)

        # ── ⑦ Source ──────────────────────────────────────────────────────────
        _add(f"🔗  {summary.source_url}", lines)
        _add("", lines)

        # ── ⑧ 4 Voices — all mindsets with content, section title from story ──
        if personas is not None:
            persona_slots = [
                ("business", "💼  Capitalist Mind",      personas.business),
                ("policy",   "🏛️  Government Mind",       personas.policy),
                ("genz",     "🎓  Generalist Mind",       personas.genz),
                ("linkedin", "🧠  Tech & Workforce Mind", personas.linkedin),
            ]
            active_pm = [
                (key, label, p)
                for key, label, p in persona_slots
                if p.perspective and p.perspective.strip()
            ]

            if active_pm:
                n = len(active_pm)

                # Section title derived from story angle — no hardcoded string.
                _VOICES_TITLE: dict[str, str] = {
                    "regulation":     f"💬  {n} takes on the regulatory angle",
                    "product_launch": f"💬  {n} takes on this launch",
                    "funding":        f"💬  {n} takes on the market move",
                    "acquisition":    f"💬  {n} takes on the deal",
                    "research":       f"💬  {n} takes on the research",
                    "other":          f"💬  {n} angles on this story",
                }
                voices_hdr = _VOICES_TITLE.get(event_type, f"💬  {n} angles on this story")
                hdr_cost   = _linkedin_len(voices_hdr) + 1 + 1
                per_persona = max(300, (remaining - hdr_cost) // n) - 1

                _add(voices_hdr, lines)
                _add("", lines)

                for key, label, p in active_pm:
                    label_cost   = _linkedin_len(label) + 1
                    persp_budget = max(PERSP_MIN, per_persona - label_cost - BLANK_COST)
                    clipped      = _clip_at_sentence(p.perspective.strip(), persp_budget)
                    _add("\n".join([label, clipped]), lines)
                    _add("", lines)

        # ── ⑨ Engagement CTA — specific, article-grounded question ───────────
        cta = _build_cta(
            headline=summary.headline,
            event_type=event_type,
            controversy=controversy,
            top_persona=top_persona,
            source=summary.source,
        )

        # ── ⑩ Dynamic hashtags — base brand + company/topic tags from article ─
        dynamic_tags = _extract_dynamic_tags(
            headline   = summary.headline,
            summary    = summary.summary,
            source     = summary.source,
            key_points = summary.key_points,
            event_type = event_type,
            max_tags   = 6,
        )
        tag_line = " ".join(dynamic_tags)
        hashtag_block = f"{self._BASE_HASHTAGS}  {tag_line}".strip() if tag_line else self._BASE_HASHTAGS

        # ── Footer assembly — always appended unconditionally ─────────────────
        footer_block = "\n".join([
            f"🗣️  {cta}",
            "",
            self._FOOTER_BASE,
            "",
            hashtag_block,
        ])

        body      = "\n".join(lines).rstrip()
        separator = "\n\n"
        max_body  = POST_LIMIT - _linkedin_len(separator) - _linkedin_len(footer_block)
        if _linkedin_len(body) > max_body:
            # Clip at the last sentence boundary so the body never ends mid-sentence.
            body = _clip_at_sentence(body, max_body)

        full_post = body + separator + footer_block
        if _linkedin_len(full_post) > POST_LIMIT:
            # Final safety guard — clip the entire post at the last sentence boundary.
            full_post = _clip_at_sentence(full_post, POST_LIMIT)
        return full_post

    # ── Comment composition ───────────────────────────────────────────────────

    @staticmethod
    def _compose_comment(label: str, perspective: str, evidence: list[str]) -> str:
        """
        Persona comment text — used when LinkedIn Comments API is available.
        Limit: 1250 chars per LinkedIn Comments API docs.
        """
        lines = [label.upper(), "", perspective]
        if evidence:
            lines.append("")
            for ev in evidence[:3]:
                lines.append(f"↳  {ev}")
        return "\n".join(lines)

    # ── Idempotency key ───────────────────────────────────────────────────────

    @staticmethod
    def _make_publication_key(article_id: str, headline: str, post_body: str = "") -> str:
        """
        Stable idempotency key: {article_id}:{YYYY-MM-DD}:{body_hash[:12]}

        Body hash ensures any content change forces a new LinkedIn post instead
        of LinkedIn returning the old URN via server-side deduplication.
        No run_id suffix — identical across CronJob retries on the same day.
        """
        today        = date.today().isoformat()
        content_hash = hashlib.sha256((post_body or headline).encode()).hexdigest()[:12]
        return f"{article_id}:{today}:{content_hash}"

    # ── Skip result ───────────────────────────────────────────────────────────

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
