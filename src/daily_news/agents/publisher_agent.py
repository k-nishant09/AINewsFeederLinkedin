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
    Build a specific, practical engagement question.

    Rules:
      - ONE focused question that people can actually answer with a concrete reply.
      - Numbered-choice format preferred — lowers the barrier to respond.
      - Derived from article signals — not a generic "what do you think?" ask.
      - controversy is lowercase ("low", "medium", "high").
    """
    is_controversial = controversy in ("high", "medium")

    # Extract a short subject phrase from the headline for specificity.
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
                "When AI safety tests show models still attempt restricted actions, who should be responsible?\n\n"
                "1️⃣ The AI lab — they built it\n"
                "2️⃣ Regulators — they set the rules\n"
                "3️⃣ Enterprises deploying it\n"
                "4️⃣ Everyone equally\n\n"
                "Where do you land? 👇"
            )
        return (
            f"What would actually make AI safer — not just compliant?\n\n"
            f"For those working with AI systems day-to-day: what guardrail do you wish existed? 👇"
        )

    if event_type == "product_launch":
        return (
            f"If you were evaluating {subject} for your team today, what would you test first?\n\n"
            f"1️⃣ Accuracy on real tasks\n"
            f"2️⃣ Cost at scale\n"
            f"3️⃣ Security & data privacy\n"
            f"4️⃣ Integration with existing tools\n\n"
            f"Drop your priority below. 👇"
        )

    if event_type in ("funding", "acquisition"):
        return (
            f"As AI investment concentrates into fewer players, what concerns you most?\n\n"
            f"1️⃣ Innovation slows down\n"
            f"2️⃣ Pricing power goes to a handful of companies\n"
            f"3️⃣ Talent concentrates in one place\n"
            f"4️⃣ Open-source alternatives get crowded out\n\n"
            f"What's your read? 👇"
        )

    if event_type == "research":
        return (
            f"For those already working with AI in production: does research like this change anything you do today — "
            f"or does it take 2-3 years to reach your stack?\n\n"
            f"Genuinely curious about the gap between research and real-world deployment. 👇"
        )

    # Fallback — 6-choice production-reality CTA tuned to Senior/Director/VP
    # practitioners in IT Services and Software Development (confirmed audience).
    # This format outperforms policy debate questions for this audience.
    _PRODUCTION_CTA = (
        "Everyone is talking about AI agents.\n\n"
        "But here's the question I keep coming back to:\n\n"
        "Can we actually operate them reliably in production?\n\n"
        "The architecture quickly becomes:\n"
        "Agent + Model + Data + Tools + Security + Observability + Governance\n\n"
        "For those building or deploying AI today — what's your biggest production challenge?\n\n"
        "1️⃣ Model quality & reliability\n"
        "2️⃣ Security & data privacy\n"
        "3️⃣ Data quality & pipelines\n"
        "4️⃣ Observability & debugging\n"
        "5️⃣ Infrastructure cost at scale\n"
        "6️⃣ Organisational adoption\n\n"
        "Drop the number + your experience below. 👇"
    )

    if top_persona == "business":
        return (
            f"If your team is evaluating AI investments right now, what's your top decision criterion?\n\n"
            f"1️⃣ ROI evidence from similar companies\n"
            f"2️⃣ Build vs buy cost analysis\n"
            f"3️⃣ Data security and compliance\n"
            f"4️⃣ Availability of internal AI talent\n"
            f"5️⃣ Speed to production\n"
            f"6️⃣ Vendor lock-in risk\n\n"
            f"What's driving the conversation at your org? 👇"
        )
    if top_persona == "policy":
        return (
            "What AI regulation would actually make things safer — not just harder to ship?\n\n"
            "1️⃣ Clear liability rules\n"
            "2️⃣ Mandatory transparency & audit rights\n"
            "3️⃣ International standards alignment\n"
            "4️⃣ Better enforcement of existing rules\n"
            "5️⃣ Independent safety testing requirements\n"
            "6️⃣ Something else — drop it below\n\n"
            "Builders, policy folks, and end users — where do you each see the gap? 👇"
        )
    if top_persona == "linkedin":
        return _PRODUCTION_CTA
    # genz / other — default to production CTA (matches confirmed audience)
    return _PRODUCTION_CTA


# ── Context line builder ─────────────────────────────────────────────────────

def _build_context_line(
    event_type: str,
    controversy: str,
    significance: float,
    relevance: float,
    source: str,
) -> str:
    """
    Build a single human-readable context line for the reader.
    Replaces the old 'Jev Decision' data block entirely.
    Plain English — no scores, no 'Jev', no jargon. Framed as editorial context.
    """
    controversy_lower = controversy.lower()

    # Significance + relevance → one plain-English editorial framing
    if significance >= 0.65 and relevance >= 0.80:
        weight = "This is one of the more significant AI developments this week."
    elif significance >= 0.45 and relevance >= 0.65:
        weight = "Worth knowing if you work in or around AI."
    else:
        weight = "A signal worth tracking as AI capabilities continue to evolve."

    controversy_note = ""
    if controversy_lower == "high":
        controversy_note = " The debate around this is genuinely polarised."
    elif controversy_lower == "medium":
        controversy_note = " Opinions in the AI community are split on this one."

    return f"{weight}{controversy_note}"


# ── Engagement hook builder ───────────────────────────────────────────────────

import hashlib as _hashlib

# Pool of 3 openers per event type — rotated by date so the same opener
# never appears two days in a row. Keeps the feed feeling fresh.
_HOOK_POOLS: dict[str, list[str]] = {
    "regulation": [
        "Most people underestimate how fast AI regulation is moving.",
        "The compliance deadline your legal team doesn't know about yet.",
        "AI governance just got real. Here's what the timeline actually looks like.",
    ],
    "product_launch": [
        "A new AI capability just dropped — and it changes what's possible.",
        "The benchmark numbers look clean. The production reality is more complicated.",
        "Something shipped today that practitioners need to evaluate, not just read about.",
    ],
    "funding": [
        "Big capital is moving fast in AI. Here's what the money is chasing.",
        "Another major AI bet. Here's what it means for everyone building on top of these platforms.",
        "When the big players consolidate, the dependency risk changes for everyone else.",
    ],
    "research": [
        "A research finding that could shift how we build AI systems.",
        "The benchmark looked impressive. The production reality is a different question.",
        "Academic paper today. Production reality in 18 months. Here's the gap worth tracking.",
    ],
    "acquisition": [
        "AI consolidation is accelerating. Here's why this deal matters.",
        "Another major acquisition. Here's what platform dependency now looks like.",
        "When the big players acquire, the ecosystem shifts for everyone building on top.",
    ],
    "other": [
        "Here's an AI story that's worth 2 minutes of your attention.",
        "Everyone is talking about AI agents. Here's the production question nobody is asking.",
        "The news cycle moved on. This story hasn't finished mattering yet.",
    ],
}


def _build_hook_line(event_type: str, headline: str, source: str) -> str:
    """
    Build a strong opening hook line — the first thing the reader sees.
    Rotates through the event-type pool using a date-based index so the
    same opener never repeats on consecutive days.
    Human-voice, not robotic. No scores, no brand names.
    """
    from datetime import date
    pool = _HOOK_POOLS.get(event_type, _HOOK_POOLS["other"])
    # Deterministic rotation: day-of-year mod pool size — same pool order per day
    day_index = date.today().timetuple().tm_yday
    return pool[day_index % len(pool)]


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
        LinkedIn Engagement-First post layout.
        Formula: Hook → Relatable problem → Insight → 3 takeaways → Practical question → CTA

        Human voice throughout. No Jev scores shown to readers. No data blocks.
        The Jev signals are used internally to pick the right hook, CTA, and hashtags
        — they never appear as visible numbers or labels in the post.

        Flow:
          ① Hook opener    — 1-2 lines that stop the scroll (event-type driven)
          ② Headline       — the specific news story
          ③ Why it matters — human "so what", not a dry summary
          ④ 3 sharp facts  — what you need to know (bullets, full sentences)
          ⑤ Real-world impact — Business / Workforce / Tech (plain English, no labels)
          ⑥ 4 Voices       — practitioner perspectives (ranked by relevance, not code order)
          ⑦ Source link
          ⑧ Engagement CTA — ONE specific practical question, not "what do you think?"
          ⑨ Footer         — minimal disclaimer + dynamic hashtags from article content
        """
        POST_LIMIT   = 2900
        HOOK_CAP     = 160   # hook opener — punchy, 1-2 lines
        SUMMARY_CAP  = 260   # why it matters — tight and human
        KP_CAP       = 160   # per key-point bullet
        IMPACT_CAP   = 160   # per impact line
        PERSP_MIN    = 220   # min budget for one complete ≤200-char persona sentence
        BLANK_COST   = 1

        # ── Extract Jev signals (internal use only — never shown as raw scores) ─
        event_type   = str((jev_scores or {}).get("event_type", "other")).lower().strip()
        relevance    = float((jev_scores or {}).get("relevance_score",    0.0))
        significance = float((jev_scores or {}).get("significance",       0.0))
        controversy  = str((jev_scores or {}).get("controversy_level", "low")).lower().strip()
        ps_scores    = (jev_scores or {}).get("persona_scores", {})
        active_p     = (jev_scores or {}).get("active_personas", [])

        # Persona display metadata
        _PMETA: dict[str, tuple[str, str, str]] = {
            "business": ("💼", "Business",       "ROI & market strategy"),
            "policy":   ("🏛️",  "Policy",         "regulation & governance"),
            "genz":     ("🎓", "Generalist",     "everyday & societal impact"),
            "linkedin": ("🧠", "Tech+Workforce", "engineering & careers"),
        }

        # Rank personas by Jev score so highest relevance appears first
        ranked: list[tuple[str, float]] = sorted(
            [(p, ps_scores.get(p, 0.0)) for p in active_p if p in _PMETA],
            key=lambda x: x[1], reverse=True,
        ) if ps_scores else []

        top_persona = ranked[0][0] if ranked else (active_p[0] if active_p else "linkedin")

        # ── Budget tracker ─────────────────────────────────────────────────────
        remaining = POST_LIMIT

        def _add(block: str, lines: list[str]) -> None:
            nonlocal remaining
            cost = _linkedin_len(block) + 1
            if cost > remaining:
                return
            lines.append(block)
            remaining -= cost

        lines: list[str] = []

        # ── ① Hook opener — stops the scroll, human voice ─────────────────────
        # Derived from event_type via _build_hook_line — no hardcoded strings.
        hook_line = _build_hook_line(event_type, summary.headline, summary.source)
        _add(_clip_at_sentence(hook_line, HOOK_CAP), lines)
        _add("", lines)

        # ── ② Headline — the specific story ───────────────────────────────────
        _add(summary.headline, lines)
        _add("", lines)

        # ── ③ Why it matters — human "so what", not a dry news summary ─────────
        why = _clip_at_sentence(summary.why_it_matters.strip(), SUMMARY_CAP)
        _add(why, lines)
        _add("", lines)

        # ── ④ Context line — editorial framing in plain English ────────────────
        # Replaces the old Jev Decision data block entirely.
        # No scores, no percentages, no "Jev" — just a human editorial note.
        context = _build_context_line(event_type, controversy, significance, relevance, summary.source)
        _add(context, lines)
        _add("", lines)

        # ── ⑤ 3 sharp facts ───────────────────────────────────────────────────
        _add("Here's what you need to know:", lines)
        _add("", lines)
        for pt in summary.key_points[:3]:
            _add(f"▸ {_clip_at_sentence(pt.strip(), KP_CAP)}", lines)
        _add("", lines)

        # ── ⑤b Real-world impact — plain English, no section label ────────────
        # Framed as what this means for people, not as a data table.
        impacts = []
        if summary.business_impact and summary.business_impact.strip():
            impacts.append(f"For businesses: {_clip_at_sentence(summary.business_impact.strip(), IMPACT_CAP)}")
        if summary.job_impact and summary.job_impact.strip():
            impacts.append(f"For practitioners: {_clip_at_sentence(summary.job_impact.strip(), IMPACT_CAP)}")
        if summary.technology_impact and summary.technology_impact.strip():
            impacts.append(f"For builders: {_clip_at_sentence(summary.technology_impact.strip(), IMPACT_CAP)}")

        for imp in impacts:
            _add(imp, lines)
        if impacts:
            _add("", lines)

        # ── ⑥ 4 Voices — ranked by Jev score, not code order ──────────────────
        # Shows who in the professional world is thinking what.
        # Persona order is driven by relevance — highest-relevance first.
        if personas is not None:
            persona_map = {
                "business": ("💼 Business view", personas.business),
                "policy":   ("🏛️ Policy view",   personas.policy),
                "genz":     ("🎓 Generalist view", personas.genz),
                "linkedin": ("🧠 Tech & careers", personas.linkedin),
            }

            # Build ordered list: Jev-ranked first, then unranked remainder
            ranked_keys = [p for p, _ in ranked]
            all_keys    = ["business", "policy", "genz", "linkedin"]
            ordered     = ranked_keys + [k for k in all_keys if k not in ranked_keys]

            active_pm = [
                (key, persona_map[key][0], persona_map[key][1])
                for key in ordered
                if key in persona_map and persona_map[key][1].perspective
                and persona_map[key][1].perspective.strip()
            ]

            if active_pm:
                n = len(active_pm)
                _add("─" * 20, lines)
                _add("Different perspectives on this:", lines)
                _add("", lines)

                hdr_cost    = _linkedin_len("Different perspectives on this:") + 1 + 1 + 21
                per_persona = max(300, (remaining - hdr_cost) // n) - 1

                for key, label, p in active_pm:
                    label_cost   = _linkedin_len(label) + 1
                    persp_budget = max(PERSP_MIN, per_persona - label_cost - BLANK_COST)
                    clipped      = _clip_at_sentence(p.perspective.strip(), persp_budget)
                    _add(f"{label}\n{clipped}", lines)
                    _add("", lines)

        # ── ⑦ Source link ─────────────────────────────────────────────────────
        _add(f"Full story → {summary.source_url}", lines)
        _add("", lines)

        # ── ⑧ Engagement CTA — ONE specific practical question ─────────────────
        cta = _build_cta(
            headline    = summary.headline,
            event_type  = event_type,
            controversy = controversy,
            top_persona = top_persona,
            source      = summary.source,
        )

        # ── ⑨ Dynamic hashtags — fully from article content, no fixed set ──────
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

        # ── Footer assembly ────────────────────────────────────────────────────
        # CTA is the last thing before the footer — drives comment intent.
        footer_block = "\n".join([
            cta,
            "",
            self._FOOTER_BASE,
            "",
            hashtag_block,
        ])

        body      = "\n".join(lines).rstrip()
        separator = "\n\n"
        max_body  = POST_LIMIT - _linkedin_len(separator) - _linkedin_len(footer_block)
        if _linkedin_len(body) > max_body:
            body = _clip_at_sentence(body, max_body)

        full_post = body + separator + footer_block
        if _linkedin_len(full_post) > POST_LIMIT:
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
