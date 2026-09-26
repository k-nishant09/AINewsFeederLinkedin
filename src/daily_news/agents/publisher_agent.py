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

from daily_news.agents.grammar_agent import GrammarAgent
from daily_news.agents.guardrails import OutputGuardrail
from daily_news.agents.published_store import published_store
from daily_news.config.settings import get_settings
from daily_news.mcp.linkedin import LinkedInMCPClient
from daily_news.models.evaluation import EvaluationResult
from daily_news.models.persona import PersonaSetOutput
from daily_news.models.summary import NewsSummary

logger = logging.getLogger(__name__)


# ── LinkedIn Unicode Bold Formatter ──────────────────────────────────────────

def _to_unicode_bold(text: str) -> str:
    """
    Convert ASCII alphanumeric characters to Unicode Mathematical Bold characters.
    LinkedIn API does NOT parse Markdown (e.g. **bold** or *italic*); it renders
    them as literal asterisk characters. Converting to Unicode Bold Mathematical
    alphanumeric characters renders native bold on LinkedIn feed across mobile & desktop.
    """
    result = []
    for ch in text:
        code = ord(ch)
        if 0x41 <= code <= 0x5A:      # 'A'-'Z'
            result.append(chr(0x1D400 + (code - 0x41)))
        elif 0x61 <= code <= 0x7A:    # 'a'-'z'
            result.append(chr(0x1D41A + (code - 0x61)))
        elif 0x30 <= code <= 0x39:    # '0'-'9'
            result.append(chr(0x1D7CE + (code - 0x30)))
        else:
            result.append(ch)
    return "".join(result)


def _convert_markdown_bold_to_unicode(text: str) -> str:
    """
    Finds all **text** patterns in text and replaces them with Unicode bold characters,
    stripping the markdown asterisks.
    """
    return re.sub(r"\*\*(.+?)\*\*", lambda m: _to_unicode_bold(m.group(1)), text)


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


# ── Dynamic SEO / AEO Hashtag Engine ──────────────────────────────────────────

def _extract_dynamic_tags(
    headline: str,
    summary: str,
    source: str,
    key_points: list[str],
    event_type: str,
    story_seo_tags: list[str] | None = None,
    max_tags: int = 7,
) -> list[str]:
    """
    SEO & AEO Dynamic Hashtag Extractor:
    Zero static company hardcoding. Extracts 100% dynamic, context-specific tags:
      1. Storyteller AI/LLM identified SEO hashtags (from dynamic_seo_hashtags).
      2. Proper nouns & Named Entities extracted dynamically from headline & key points (PascalCase).
      3. Source publication hashtag (e.g. #SiliconANGLE, #TechCrunch, #Reuters, #Bloomberg).
      4. Topic & Domain reference tags derived from headline subjects.
    """
    found_tags: list[str] = []
    seen_lower: set[str] = set()

    # 1. First priority: Storyteller AI-generated SEO hashtags
    if story_seo_tags:
        for tag in story_seo_tags:
            cleaned = tag.strip()
            if not cleaned:
                continue
            if not cleaned.startswith("#"):
                cleaned = f"#{cleaned}"
            # Clean non-alphanumeric except hash
            cleaned = "#" + re.sub(r"[^A-Za-z0-9]", "", cleaned[1:])
            tag_lower = cleaned.lower()
            if len(cleaned) > 2 and tag_lower not in seen_lower:
                seen_lower.add(tag_lower)
                found_tags.append(cleaned)
            if len(found_tags) >= max_tags:
                return found_tags

    # 2. Source Publication Tag
    if source:
        clean_src = re.sub(r"[^A-Za-z0-9]", "", source)
        if clean_src and len(clean_src) > 2:
            src_tag = f"#{clean_src[0].upper()}{clean_src[1:]}"
            if src_tag.lower() not in seen_lower:
                seen_lower.add(src_tag.lower())
                found_tags.append(src_tag)

    # 3. Dynamic Proper Nouns & Capitalized Entities in Headline (e.g. "Okta", "DeepSeek", "Mistral", "Dex")
    headline_tokens = re.findall(r"\b[A-Z][a-zA-Z0-9_-]+\b", headline)
    _GENERIC_WORDS = {"a", "an", "the", "in", "on", "at", "to", "for", "with", "by", "from", "and", "or", "new", "ai", "how", "why", "what", "says", "said", "turns", "into"}
    for tok in headline_tokens:
        tok_clean = re.sub(r"[^A-Za-z0-9]", "", tok)
        if tok_clean.lower() not in _GENERIC_WORDS and len(tok_clean) > 2:
            tag = f"#{tok_clean}"
            if tag.lower() not in seen_lower:
                seen_lower.add(tag.lower())
                found_tags.append(tag)
        if len(found_tags) >= max_tags:
            break

    # 4. Contextual Event / Action Tag
    _EVENT_AEO = {
        "product_launch": ["AIProductLaunch", "EnterpriseAI"],
        "funding":        ["AIFunding", "VentureCapital"],
        "regulation":     ["AIRegulation", "AIGovernance"],
        "research":       ["AIResearch", "MachineLearning"],
        "acquisition":    ["MergersAndAcquisitions", "TechNews"],
        "other":          ["AIStrategy", "TechInnovation"],
    }
    for ev_tag in _EVENT_AEO.get(event_type, ["AIStrategy"]):
        if len(found_tags) >= max_tags:
            break
        tag = f"#{ev_tag}"
        if tag.lower() not in seen_lower:
            seen_lower.add(tag.lower())
            found_tags.append(tag)

    # 5. Core Searchable Foundation
    for base in ["AI", "GenerativeAI"]:
        if len(found_tags) >= max_tags:
            break
        tag = f"#{base}"
        if tag.lower() not in seen_lower:
            seen_lower.add(tag.lower())
            found_tags.append(tag)

    return found_tags


# ── Dynamic CTA generator ─────────────────────────────────────────────────────

def _extract_subject(headline: str, max_words: int = 5) -> str:
    """
    Derive a clean, readable topic label from the headline.

    Rules:
    - Use only the first clause (split at comma/colon/em-dash) so multi-event
      headlines don't bleed into each other.
    - Trim common action verbs (Announces, Raises, Shows, Rejects, Cites…) so
      the result reads as a noun phrase, not a sentence fragment.
    - Keep company/proper names intact (NVIDIA, OpenAI, EU AI Act…).
    - Return at most max_words words.
    - Short headlines (≤ 6 words) are returned in full.
    """
    import re

    # Take only the first clause — split at , ; : — (em-dash) before processing
    first_clause = re.split(r"[,;:\u2014\u2013]", headline)[0].strip().rstrip(".,;:!?")

    words = first_clause.split()
    if not words:
        return "this development"

    # Short first clause — use as-is
    if len(words) <= 4:
        return first_clause

    # Action verbs that appear mid-headline and mark where the subject ends
    _ACTION_VERBS = {
        "announces", "announced", "raises", "raised", "rejects", "rejected",
        "cites", "cited", "launches", "launched", "releases", "released",
        "shows", "reveals", "revealed", "reports", "reported",
        "acquires", "acquired", "invests", "invested", "deploys", "deployed",
        "publishes", "published", "warns", "warned", "says", "said",
        "probes", "probed", "exploits", "exploited", "unveils", "unveiled",
        "cuts", "expands", "calls", "claims", "targets", "plans",
        "outperforms", "surpasses", "beats", "tops", "hits", "misses",
        "fires", "hires", "buys", "sells", "closes", "opens", "backs",
        "faces", "files", "sues", "joins", "leaves", "wins", "loses",
        "partners", "secures", "takes", "gets", "makes", "sets", "brings",
    }

    # Find first action-verb; take words before it as the subject
    split_at = len(words)
    for i, w in enumerate(words):
        if w.lower().rstrip(".,;:!?") in _ACTION_VERBS and i > 0:
            split_at = i
            break

    candidate_words = [w.rstrip(".,;:!?") for w in words[:min(split_at, max_words)]]
    # If verb-split gives only 1 word (e.g. "NVIDIA"), extend with next 1-2 noun words
    if len(candidate_words) == 1 and split_at < len(words):
        extra = [w.rstrip(".,;:!?") for w in words[split_at + 1: split_at + 3]
                 if w.lower().rstrip(".,;:!?") not in _ACTION_VERBS]
        candidate_words += extra[:2]

    phrase = " ".join(candidate_words).rstrip(",;: ")
    return phrase if phrase else first_clause[:50]


def _build_cta(
    headline: str,
    event_type: str,
    controversy: str,
    top_persona: str,
    source: str,
    missing_angle: str = "",
    recommended_audience: str = "",
) -> str:
    """
    Build a 100% article-specific engagement question.

    Rules:
      - Every choice and question is derived from THIS article's headline,
        event_type, and intelligence signals.
      - No static / hardcoded question text.
      - Numbered-choice format lowers the barrier to reply.
      - controversy is lowercase ("low", "medium", "high").
    """
    is_controversial = controversy in ("high", "medium")
    subject = _extract_subject(headline)
    audience = recommended_audience.strip() or "practitioners"
    angle = missing_angle.strip()

    # ── Regulation ────────────────────────────────────────────────────────────
    if event_type == "regulation":
        if is_controversial:
            return (
                f"Who should be accountable when {subject} goes wrong?\n\n"
                f"1️⃣ The company that built it\n"
                f"2️⃣ The regulator that approved it\n"
                f"3️⃣ The enterprise that deployed it\n"
                f"4️⃣ Shared liability across all three\n\n"
                f"Where do you land? 👇"
            )
        return (
            f"If you were writing the rulebook on {subject}, what would the first guardrail be?\n\n"
            f"Drop your answer below — especially if you work with these systems day-to-day. 👇"
        )

    # ── Product launch ────────────────────────────────────────────────────────
    if event_type == "product_launch":
        return (
            f"If your team were evaluating {subject} this week, what's the first thing you'd test?\n\n"
            f"1️⃣ Accuracy on real production tasks\n"
            f"2️⃣ Total cost at scale\n"
            f"3️⃣ Security and data handling\n"
            f"4️⃣ How quickly it integrates with existing systems\n\n"
            f"What's your priority? 👇"
        )

    # ── Funding / Acquisition ─────────────────────────────────────────────────
    if event_type in ("funding", "acquisition"):
        return (
            f"When {subject} consolidates further, what's the real risk for teams building on top?\n\n"
            f"1️⃣ Pricing power shifts — costs go up\n"
            f"2️⃣ Roadmap changes — features you rely on disappear\n"
            f"3️⃣ Data access tightens\n"
            f"4️⃣ Open alternatives get marginalised\n\n"
            f"What concerns you most? 👇"
        )

    # ── Research ──────────────────────────────────────────────────────────────
    if event_type == "research":
        return (
            f"Research on {subject} is advancing fast.\n\n"
            f"For those already deploying AI: does work like this change anything in your stack today — "
            f"or does it typically take 18–24 months before it reaches production?\n\n"
            f"Curious about the gap between paper and practice. 👇"
        )

    # ── Other / fallback — fully derived from headline + intelligence signals ─
    # Use the missing_angle from Jev as the question when available.
    if angle:
        # Jev identified a missing perspective — turn it into the question
        return (
            f"{angle}\n\n"
            f"If you work in or around AI — what's your take? 👇"
        )

    # Final fallback: headline-anchored open question, persona-tuned
    if top_persona == "business":
        return (
            f"How does {subject} actually change the business case for AI at your organisation?\n\n"
            f"1️⃣ It accelerates our roadmap\n"
            f"2️⃣ It raises new compliance questions\n"
            f"3️⃣ We're still evaluating the ROI\n"
            f"4️⃣ It doesn't change much for us yet\n\n"
            f"What's your read? 👇"
        )
    if top_persona == "policy":
        return (
            f"What's the policy gap that {subject} exposes?\n\n"
            f"1️⃣ Liability and accountability\n"
            f"2️⃣ Transparency and audit rights\n"
            f"3️⃣ Cross-border standards alignment\n"
            f"4️⃣ Enforcement of existing rules\n\n"
            f"Where do you see the biggest gap? 👇"
        )
    # linkedin / genz / default — production-reality angle, headline-anchored
    return (
        f"For those building or deploying AI today: what does {subject} change in practice?\n\n"
        f"1️⃣ How we evaluate new tools\n"
        f"2️⃣ How we think about cost and scale\n"
        f"3️⃣ How we handle security and privacy\n"
        f"4️⃣ How we explain AI decisions internally\n"
        f"5️⃣ Honestly — not much yet\n\n"
        f"Drop your number + a sentence below. 👇"
    )


# ── Context line builder ─────────────────────────────────────────────────────

def _build_context_line(
    event_type: str,
    controversy: str,
    significance: float,
    relevance: float,
    source: str,
    headline: str = "",
) -> str:
    """
    Build a single human-readable context line for the reader.
    Derived from article signals — not generic boilerplate.
    Plain English — no scores, no 'Jev', no jargon.
    """
    controversy_lower = controversy.lower()
    subject = _extract_subject(headline, max_words=4) if headline else ""
    src = source.strip() if source else ""

    # Significance + relevance + event_type → specific editorial framing
    if significance >= 0.65 and relevance >= 0.80:
        if event_type == "product_launch":
            weight = f"One of the more meaningful AI capability releases this week{' from ' + src if src else ''}."
        elif event_type == "research":
            weight = f"Research that warrants attention — this is the kind of finding that shapes production decisions in 12–18 months."
        elif event_type in ("funding", "acquisition"):
            weight = f"A deal that concentrates AI capability — and changes the dependency picture for builders."
        elif event_type == "regulation":
            weight = f"A regulatory development with real teeth — worth understanding before it hits your compliance team."
        else:
            weight = f"One of the more significant AI developments this week{' via ' + src if src else ''}."
    elif significance >= 0.45 and relevance >= 0.65:
        if event_type == "product_launch":
            weight = f"A new AI capability worth 5 minutes of your time{' from ' + src if src else ''}."
        elif event_type == "research":
            weight = f"A research signal worth tracking{' from ' + src if src else ''} — early-stage but directionally important."
        elif event_type in ("funding", "acquisition"):
            weight = f"Capital moving into AI in a way that shifts the ecosystem — worth understanding the implications."
        elif event_type == "regulation":
            weight = f"A policy development that will land on engineering and product teams, not just legal."
        else:
            weight = f"Worth understanding if {subject} is on your radar."
    else:
        if event_type == "research":
            weight = "An early signal — too soon to act on, but worth adding to your reading list."
        else:
            weight = f"A development worth a quick read{' from ' + src if src else ''} as AI capabilities continue to shift."

    controversy_note = ""
    if controversy_lower == "high":
        controversy_note = " The debate on this is genuinely polarised — senior voices on both sides."
    elif controversy_lower == "medium":
        controversy_note = " Not everyone in the AI community agrees on what this means."

    return f"{weight}{controversy_note}"


# ── Engagement hook builder ───────────────────────────────────────────────────

import hashlib as _hashlib


def _build_hook_line(event_type: str, headline: str, source: str, sentiment: str = "neutral") -> str:
    """
    Build a strong, article-specific opening hook — the first thing the reader sees.

    Every hook is derived from the actual headline + event_type + sentiment.
    No static string is ever returned — each hook is unique to this article.

    Approach:
      - Extracts the most specific noun phrase from the headline.
      - Applies a sentiment-register + event-type framing template.
      - Rotates across 5 template variants using a headline-hash to avoid
        the same pattern appearing on consecutive posts.
    """
    import hashlib as _hlib
    s = (sentiment or "neutral").lower().strip()
    if s not in ("positive", "negative"):
        s = "neutral"

    subject = _extract_subject(headline, max_words=5)
    src = source.strip() if source else ""

    # Hash the headline to pick a rotation slot — deterministic per article,
    # never repeats across different headlines on the same day.
    slot = int(_hlib.md5(headline.encode()).hexdigest(), 16) % 5

    # ── Regulation ────────────────────────────────────────────────────────────
    if event_type == "regulation":
        templates = {
            "positive": [
                f"The AI policy development around {subject} is further along than most teams realise.",
                f"A regulatory shift on {subject} — and this one actually has enforcement behind it.",
                f"The compliance conversation around {subject} just changed.",
                f"This is the AI governance update worth reading before it reaches your legal team.",
                f"AI policy on {subject} is moving. Here's what changed.",
            ],
            "negative": [
                f"The {subject} story is a compliance problem your legal team hasn't flagged yet.",
                f"Regulation on {subject} is tightening — and the enforcement gap is closing faster than expected.",
                f"Most organisations are not ready for what just happened with {subject}.",
                f"The regulatory pressure building around {subject} is real — and it's arriving faster than roadmaps allow.",
                f"This is the {subject} development that will land on engineering teams, not just policy ones.",
            ],
            "neutral": [
                f"The regulatory picture around {subject} just shifted.",
                f"Most people underestimate how fast governance on {subject} is moving.",
                f"Something changed in the AI policy landscape around {subject}.",
                f"Here's what the {subject} regulatory update actually means for practitioners.",
                f"The rules around {subject} are changing. Here's what matters.",
            ],
        }

    # ── Product launch ────────────────────────────────────────────────────────
    elif event_type == "product_launch":
        templates = {
            "positive": [
                f"{subject} just shipped — and it changes what's possible in production.",
                f"A new AI capability around {subject} dropped. Here's what practitioners should evaluate.",
                f"The capability gap around {subject} just narrowed.",
                f"Something in {subject} shipped today that deserves more than a quick read.",
                f"{subject} is now available. Here's the part worth paying attention to.",
            ],
            "negative": [
                f"The {subject} launch numbers look clean. The production reality is more complicated.",
                f"{subject} shipped — here's the part the press release didn't cover.",
                f"Another {subject} release with open questions about real-world reliability.",
                f"The {subject} benchmark is impressive. The integration story still needs work.",
                f"What the {subject} announcement gets right — and what it leaves unanswered.",
            ],
            "neutral": [
                f"{subject} is worth 5 minutes of your time before you decide to skip it.",
                f"Something shipped around {subject} today. Here's what's actually useful.",
                f"The {subject} release deserves a closer read than the headlines suggest.",
                f"A new {subject} capability — here's what it means for teams building on top.",
                f"{subject} just moved. Here's the practical implication.",
            ],
        }

    # ── Funding ───────────────────────────────────────────────────────────────
    elif event_type == "funding":
        templates = {
            "positive": [
                f"Significant capital just moved toward {subject}. Here's the thesis.",
                f"The funding bet on {subject} tells you something about where AI is heading.",
                f"Big money is moving into {subject} — and the strategic logic is worth understanding.",
                f"A major AI investment in {subject} that has a clear directional thesis.",
                f"{subject} just attracted significant capital. Here's what the market is signalling.",
            ],
            "negative": [
                f"Investment in {subject} is concentrating — and the dependency risk just changed.",
                f"When capital moves into {subject} at this scale, the platform lock-in question gets louder.",
                f"The {subject} investment round is significant. So are the downstream risks.",
                f"More capital concentrating in {subject} — here's who is now more exposed.",
                f"AI investment is narrowing around {subject}. Here's what that means if you build on top.",
            ],
            "neutral": [
                f"Capital just moved toward {subject}. Here's what the strategic bet actually means.",
                f"Another major AI investment in {subject}. Here's the logic — and the open questions.",
                f"The {subject} funding round deserves more than a headline skim.",
                f"Big capital moving into {subject} — here's what it tells us about where things are heading.",
                f"A significant investment in {subject}. Here's the practical implication for builders.",
            ],
        }

    # ── Acquisition ───────────────────────────────────────────────────────────
    elif event_type == "acquisition":
        templates = {
            "positive": [
                f"The {subject} acquisition makes strategic sense — and changes the ecosystem.",
                f"AI consolidation around {subject} is accelerating in a way that benefits builders.",
                f"A deal around {subject} that concentrates capability in a useful direction.",
                f"The {subject} acquisition just changed the competitive picture.",
                f"This is the {subject} deal worth understanding before the market digests it.",
            ],
            "negative": [
                f"The {subject} acquisition just changed the dependency picture for everyone building on top.",
                f"When {subject} consolidates at this scale, the platform risk changes for everyone else.",
                f"Another AI acquisition around {subject} — and the lock-in question just got louder.",
                f"AI consolidation in {subject} is accelerating. Here's who is now more exposed.",
                f"The {subject} deal is done. Here's what the market dependency now looks like.",
            ],
            "neutral": [
                f"AI consolidation around {subject} just accelerated. Here's why this deal matters.",
                f"The {subject} acquisition — here's what platform dependency now looks like.",
                f"A significant deal in {subject}. Here's what it means for builders and operators.",
                f"Another major move in {subject}. Here's the strategic logic.",
                f"The {subject} consolidation story just got a new chapter.",
            ],
        }

    # ── Research ──────────────────────────────────────────────────────────────
    elif event_type == "research":
        templates = {
            "positive": [
                f"A research finding on {subject} that could shift how we build AI systems.",
                f"The {subject} paper worth reading before it becomes a product.",
                f"Academic work on {subject} that becomes a production constraint in 12–18 months.",
                f"Research on {subject} that deserves more attention than it's getting.",
                f"A technical result on {subject} — directionally important, even if the path to production is long.",
            ],
            "negative": [
                f"The {subject} benchmark looked clean. The production reality is a different question.",
                f"A {subject} research result that raises more questions than it answers.",
                f"The {subject} paper is out. The gap between this and production is bigger than the abstract suggests.",
                f"What the {subject} research actually says — versus what the summaries claim.",
                f"The {subject} result is interesting. The production implications are complicated.",
            ],
            "neutral": [
                f"Research on {subject} that warrants 10 minutes before it becomes everyone's assumption.",
                f"A {subject} technical result that matters — the path to production is still long.",
                f"The {subject} paper that's worth reading before it lands in every pitch deck.",
                f"Early-stage research on {subject} worth tracking as it moves toward deployment.",
                f"A {subject} finding that shapes production decisions — in about 18 months.",
            ],
        }

    # ── Other / catch-all — fully headline-derived ────────────────────────────
    else:
        templates = {
            "positive": [
                f"The {subject} development is further along than most people realise.",
                f"A {subject} story that's easy to miss — and harder to ignore once you understand it.",
                f"Here's what actually changed with {subject} — without the hype.",
                f"The {subject} update worth 5 minutes of your time.",
                f"Something shifted in the {subject} space this week.",
            ],
            "negative": [
                f"The {subject} story didn't get the coverage it deserved.",
                f"The news cycle moved on from {subject}. The implications haven't.",
                f"What the {subject} story actually means — past the headline.",
                f"The {subject} development that practitioners should be paying attention to.",
                f"Most organisations haven't caught up with what just happened in {subject}.",
            ],
            "neutral": [
                f"The {subject} development is worth 5 minutes of your attention.",
                f"Something changed in the {subject} space. Here's what matters.",
                f"The {subject} story that's worth reading before the market catches up.",
                f"A {subject} development that's easy to scroll past — and shouldn't be.",
                f"Here's the {subject} update that actually changes the practical picture.",
            ],
        }

    pool = templates.get(s, templates["neutral"])
    return pool[slot % len(pool)]


def _build_sentiment_signal_line(
    sentiment: str | None,
    sentiment_stats: dict | None,
    novelty: float = 0.0,
    trend_velocity: float = 0.0,
) -> str | None:
    """
    Build a one-line editorial signal for the reader based on intelligence signals.

    Returns None when no meaningful signal is present.
    Only shown when the dominant sentiment is non-neutral and high-confidence,
    or when novelty/trend signals are strong enough to surface.
    This is NOT a score readout — it is a human-readable editorial note.
    """
    if not sentiment:
        return None

    s = sentiment.lower().strip()

    # Require a meaningful confidence margin before surfacing the sentiment signal
    if sentiment_stats:
        dominant_conf = sentiment_stats.get(s, 0.0)
        if dominant_conf < 0.55:
            return None   # signal too weak — skip the line

    if s == "positive":
        if trend_velocity >= 0.75:
            return "📈 Sentiment is running ahead of the headline — this one is accelerating fast."
        return "📈 The broader signal on this story is positive and holding."
    if s == "negative":
        if novelty >= 0.75:
            return "⚠️ This concern is genuinely new — not a repackaging of familiar risk."
        return "⚠️ The practitioner read on this story leans cautious."
    # neutral — not interesting enough to surface explicitly
    return None


def _intel_get(intel: "Any | None", key: str, default: "Any" = 0.0) -> "Any":
    """Dict-safe accessor for NewsIntelligence (may be a Pydantic model or a plain dict after LangGraph serialization)."""
    if intel is None:
        return default
    if isinstance(intel, dict):
        return intel.get(key, default)
    return getattr(intel, key, default)


def _intel_sub(intel: "Any | None", key: str, subkey: str, default: "Any" = 0.0) -> "Any":
    """Dict-safe nested accessor for NewsIntelligence sub-objects (emotion, impact, content_opportunity)."""
    sub = _intel_get(intel, key, None)
    if sub is None:
        return default
    if isinstance(sub, dict):
        return sub.get(subkey, default)
    return getattr(sub, subkey, default)


def _build_intelligence_line(intel: "Any | None", headline: str = "") -> str | None:
    """
    Build a one-line intelligence note from the NewsIntelligence object.
    Only surfaced when novelty OR trend_velocity is unusually high (≥ 0.80).
    Anchored to the article topic — not a generic score readout.
    """
    if intel is None:
        return None
    novelty  = float(_intel_get(intel, "novelty",        0.0) or 0.0)
    velocity = float(_intel_get(intel, "trend_velocity", 0.0) or 0.0)
    subject  = _extract_subject(headline, max_words=4) if headline else "this topic"
    if novelty >= 0.80 and velocity >= 0.75:
        return f"🔬 {subject} is moving faster than the news cycle — this story is ahead of most coverage."
    if novelty >= 0.80:
        return f"🔬 The {subject} angle here is genuinely new — not an incremental update on familiar ground."
    if velocity >= 0.85:
        return f"⚡ {subject} is accelerating — the pace of change on this is faster than it looks."
    return None


def _build_content_angle_line(intel: "Any | None") -> tuple[str | None, str | None]:
    """
    Extract the content angle from the NewsIntelligence content_opportunity.
    Returns (missing_angle_line, recommended_audience_line) or (None, None).
    """
    if intel is None:
        return None, None
    missing  = str(_intel_sub(intel, "content_opportunity", "missing_angle",        "") or "")
    audience = str(_intel_sub(intel, "content_opportunity", "recommended_audience", "") or "")
    angle_line    = f"📌 {missing}" if missing.strip() else None
    audience_line = f"🎯 Primary audience: {audience}" if audience.strip() else None
    return angle_line, audience_line


# ── Main Publisher Agent ──────────────────────────────────────────────────────

# ── Banned opener patterns (deterministic pre-publish scanner) ────────────────
# These are the throat-clearing / anecdote openers that the LLM judge should
# catch, but which we also block deterministically BEFORE the post is assembled.
# Any persona text that begins with one of these triggers a hard REGENERATE
# by injecting a sentinel that the OutputGuardrail blocks.
_BANNED_OPENERS: tuple[str, ...] = (
    "when i was scaling",
    "when i was running",
    "when i was building",
    "when i was at",
    "when we deployed",
    "when we rolled out",
    "when we launched",
    "when we built",
    "when we were",
    "imagine you are",
    "imagine you're",
    "imagine a startup",
    "imagine running",
    "imagine you had",
    "consider a scenario",
    "let me paint a picture",
    "let me be clear",
    "let's dive in",
    "let us dive in",
    "i've been in",
    "i have been in",
    "i was recently",
    "i recently",
    "sounds great on paper",
    "sounds promising on paper",
)

_BANNED_INLINE_PHRASES: tuple[str, ...] = (
    "the real question is whether",
    "the real question is,",
    "the real challenge lies in",
    "the real challenge is",
    "the challenge lies in",
    "the key challenge is",
    "sounds great, but",
    "sounds promising, but",
    "sounds revolutionary",
    "but the real test is",
    "but the real question",
    "at the end of the day",
    "it remains to be seen",
    "only time will tell",
    "the potential is there",
    "the potential here is",
    "the promise is great",
    "what remains to be seen",
    "in the end, more tools",
    "in the end, this",
    "in the end,",
    "the real bottleneck",
    "the real implication",
    "the real business implication",
    "the real shift here",
    "the real concern",
    "more tools do not always",
    "my advice to",
    "the key metric is",
    # metric/business variants Qwen gravitates to
    "the real business metric",
    "the key business metric",
    "the real test is whether",
    "the real issue is",
    "the real problem is",
    "the real risk is whether",
    "the real opportunity is",
)


# Regex that catches ALL "the real <noun/adj>" constructions.
# Qwen endlessly generates new variants ("the real bottleneck", "the real shift",
# "the real implication", etc.) — this one pattern covers all of them.
import re as _re
_REAL_PATTERN = _re.compile(
    r"\bthe real\s+\w+",   # "the real X" where X is any single word
    _re.IGNORECASE,
)
# "in the end" is a cliché filler regardless of what follows
_IN_THE_END_PATTERN = _re.compile(r"\bin the end\b", _re.IGNORECASE)


def _check_persona_text(text: str) -> str | None:
    """
    Deterministic banned-phrase scanner.

    Returns the offending phrase if found, else None.
    Checks:
      1. Opening-line openers (anecdote / hypothetical / throat-clearing)
      2. Inline phrases that are banned from all personas
      3. Regex catch-alls: "the real <X>" and "in the end"

    Called before post assembly so a contaminated persona triggers
    REGENERATE via OutputGuardrail BANNED_CONTENT sentinel, never
    making it to LinkedIn.
    """
    lowered = text.lower().strip()
    # Check banned openers (first 120 chars only — opener check)
    opening = lowered[:120]
    for phrase in _BANNED_OPENERS:
        if opening.startswith(phrase):
            return phrase
    # Also check if the opener appears after a very short preamble (e.g. a comma)
    # by scanning the first sentence only
    first_sentence_end = min(
        next((i for i, c in enumerate(lowered) if c in ".!?\n"), len(lowered)),
        200,
    )
    first_sentence = lowered[:first_sentence_end]
    for phrase in _BANNED_OPENERS:
        if phrase in first_sentence:
            return phrase
    # Inline banned phrases — scan full text
    for phrase in _BANNED_INLINE_PHRASES:
        if phrase in lowered:
            return phrase
    # Regex catch-alls — covers all "the real X" and "in the end" variants
    m = _REAL_PATTERN.search(text)
    if m:
        return m.group(0).lower()
    m = _IN_THE_END_PATTERN.search(text)
    if m:
        return "in the end"
    return None


class PublisherAgent:

    # Base brand footer
    _FOOTER_BASE = (
        "🤖 AIFeeders · Daily AI Intelligence · Powered by Jev\n"
        "*AI-simulated perspectives for discussion — not professional advice.*"
    )

    # Core brand fallback hashtags
    _BASE_HASHTAGS = "#AI #AIInfrastructure #Tech"

    # Character labels + emoji per persona key
    _CHAR_DIALOGUE: dict[str, tuple[str, str]] = {
        "business": ("💼", "FOUNDER"),
        "linkedin": ("🧑‍💻", "ENGINEER"),
        "genz":     ("⚖️", "SKEPTIC"),
        "policy":   ("🏛️", "POLICY"),
    }

    # Ordered persona sequence for LinkedIn Comments API (sequential, not gather)
    # Same ordering as preferred_lead in _EVENT_COMPOSITION for consistency.
    _PERSONA_ORDER: list[tuple[str, str]] = [
        ("business", "💼 FOUNDER"),
        ("linkedin", "🧑‍💻 ENGINEER"),
        ("genz",     "⚖️ SKEPTIC"),
        ("policy",   "🏛️ POLICY"),
    ]

    # ── Dynamic composition rules ─────────────────────────────────────────────
    # Maps event_type → (preferred_lead, must_include, optional_drop)
    # preferred_lead  : persona that opens the debate (highest credibility for this event)
    # must_include    : always present regardless of Jev score
    # optional_drop   : dropped when going to 3 voices (lowest topical relevance)
    _EVENT_COMPOSITION: dict[str, dict] = {
        "product_launch": {
            "preferred_lead": "linkedin",   # Engineer leads — it's a shipping story
            "must_include":   ["linkedin", "genz"],
            "optional_drop":  "policy",     # policy least urgent for new tools
        },
        "funding": {
            "preferred_lead": "business",   # Founder leads — it's a capital story
            "must_include":   ["business", "genz"],
            "optional_drop":  "policy",
        },
        "acquisition": {
            "preferred_lead": "business",
            "must_include":   ["business", "genz"],
            "optional_drop":  "linkedin",
        },
        "regulation": {
            "preferred_lead": "policy",     # Policy leads — it's a governance story
            "must_include":   ["policy", "linkedin"],
            "optional_drop":  "genz",
        },
        "research": {
            "preferred_lead": "linkedin",   # Engineer leads — it's a technical paper
            "must_include":   ["linkedin", "business"],
            "optional_drop":  "policy",
        },
        "other": {
            "preferred_lead": "business",
            "must_include":   ["business", "linkedin"],
            "optional_drop":  "policy",
        },
    }

    def __init__(self) -> None:
        self._client   = LinkedInMCPClient()
        self._settings = get_settings()
        self._grammar  = GrammarAgent()

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

        # Use pre-composed + repaired text if score_reach already built it
        _repaired = getattr(personas, "_repaired_post", None)
        if not _repaired and isinstance(personas, dict):
            _repaired = personas.get("_repaired_post")
        main_text = _repaired or self._compose_main_post(summary, personas, jev_scores=jev_scores)

        # ── Grammar correction pass — best-effort, never blocks publish ───────
        main_text = await self._grammar.correct(
            main_text,
            run_id=run_id,
            article_id=summary.article_id,
        )

        # ── Output Guardrail validation ───────────────────────────────────────
        out_guard = OutputGuardrail.inspect_output(main_text)
        if not out_guard.is_safe:
            logger.warning(
                "[%s] OutputGuardrail BLOCKED post article=%s violations=%s",
                run_id, summary.article_id, out_guard.violations,
            )
            return self._skipped_result(summary, f"output_guardrail_block:{','.join(out_guard.violations)}")

        # Convert **bold** markdown to native Unicode bold before publishing to LinkedIn
        main_text = _convert_markdown_bold_to_unicode(main_text)

        publication_key = self._make_publication_key(summary.article_id, summary.headline, main_text)

        logger.info(
            "[%s] post composed article=%s python_len=%d linkedin_utf16_len=%d%s",
            run_id, summary.article_id, len(main_text), _linkedin_len(main_text),
            " (reach-repaired)" if _repaired else "",
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
        Dialogue-delivery LinkedIn post format — build #83.

        Format: Media Person (narrator) → 4 character dialogues → Media Person close → audience Q.

        The post reads like one page of a news programme in text. The Media Person
        sets the scene, then asks each character to speak from their real-world
        experience. Each character's voice is a story passage, not a bullet point.
        The Media Person closes with the question that invites audience participation.

        Flow:
          ① HOOK              — Media Person story hook (curiosity-first, not headline repeat)
          ② SITUATION         — what actually happened + why it matters now
          ③ HUMAN ANALOGY     — makes the story relatable and concrete
          ④ MEDIA INTRO LINE  — "So I asked four people..."
          ⑤ 4 CHARACTER DIALOGUES — each prefixed with Media Person intro + character label
          ⑥ MEDIA CLOSE       — the big-picture so-what
          ⑦ SOURCE LINK
          ⑧ AUDIENCE QUESTION — future_question or CTA builder
          ⑨ FOOTER            — minimal, dynamic hashtags

        Story fields from MediaStorytellerAgent are used first.
        Fallback to legacy NewsSummary fields when story is not available.
        """
        POST_LIMIT = 2800   # Stay well inside the 3000 UTF-16 unit hard limit

        # ── Extract Jev signals ───────────────────────────────────────────────
        event_type   = str((jev_scores or {}).get("event_type", "other")).lower().strip()
        controversy  = str((jev_scores or {}).get("controversy_level", "low")).lower().strip()
        ps_scores    = (jev_scores or {}).get("persona_scores", {})
        active_p     = (jev_scores or {}).get("active_personas", [])

        # ── Pull NewsIntelligence backbone and NewsStory objects ──────────────
        intel = getattr(summary, "intelligence", None)

        story = getattr(summary, "story", None)
        if story is None and intel is not None:
            story = _intel_get(intel, "story", None)

        def _story(field: str, fallback: str = "") -> str:
            """Safe accessor for NewsStory fields — works on Pydantic model or dict."""
            if story is None:
                return fallback
            if isinstance(story, dict):
                return str(story.get(field, fallback) or fallback).strip()
            return str(getattr(story, field, fallback) or fallback).strip()

        # ── Resolved sentiment ────────────────────────────────────────────────
        nd_sentiment       = getattr(summary, "sentiment",       None) or "neutral"
        nd_sentiment_stats = getattr(summary, "sentiment_stats", None) or {}

        # ── Dynamic voice selection ───────────────────────────────────────────
        # Select 3 or 4 voices based on event_type + controversy + Jev persona scores.
        # Composition (lead order + count) adapts to the story — not fixed.
        comp = self._EVENT_COMPOSITION.get(event_type, self._EVENT_COMPOSITION["other"])
        preferred_lead = comp["preferred_lead"]
        must_include   = set(comp["must_include"])
        optional_drop  = comp["optional_drop"]

        # Always start with all 4 candidates; filter to those with real content
        all_keys = ["business", "linkedin", "genz", "policy"]

        # Use Jev persona scores to rank when available; else equal weight
        def _jev_score(key: str) -> float:
            return float(ps_scores.get(key, 0.0)) if ps_scores else 0.0

        # High controversy → keep skeptic (genz) regardless of score
        is_controversial = controversy in ("high", "medium")
        if is_controversial:
            must_include.add("genz")

        # Decide voice count: 4 if controversial OR all 4 personas scored above 0.4;
        # else 3 (drop optional_drop unless it's in must_include)
        all_scored_high = ps_scores and all(_jev_score(k) >= 0.4 for k in all_keys)
        use_four = is_controversial or bool(all_scored_high)

        if use_four:
            ordered_keys = all_keys[:]
        else:
            ordered_keys = [k for k in all_keys if k != optional_drop or k in must_include]

        # Re-order: preferred_lead first, then must_include, then the rest by Jev score
        def _sort_key(key: str) -> tuple:
            lead_priority   = 0 if key == preferred_lead else 1
            must_priority   = 0 if key in must_include else 1
            score_desc      = -_jev_score(key)
            natural_order   = all_keys.index(key)
            return (lead_priority, must_priority, score_desc, natural_order)

        ordered_keys.sort(key=_sort_key)

        # Per-voice character limit: fewer voices → more room per voice
        voice_clip = 240 if len(ordered_keys) >= 4 else 300

        top_persona = preferred_lead
        subject = _extract_subject(summary.headline, max_words=4)

        # ── Build the post as a list of paragraphs ────────────────────────────
        parts: list[str] = []

        # ── Header: Brand Headline Show Identity ──────────────────────────────
        parts.append("🧠 **AIFEEDERS | THE DAILY AI DEBATE**")

        # ── ① HOOK — Immediate tension / curiosity gap (Zero throat-clearing) ──
        media_opening = _story("media_host_opening") or _story("hook")
        if not media_opening:
            media_opening = _build_hook_line(event_type, summary.headline, summary.source, sentiment=nd_sentiment)
        
        # Clean quotes and strip generic throat-clearing
        opening_clean = media_opening.strip().strip('"')
        parts.append(f"🚨 **{opening_clean}**")

        # ── ② SITUATION & CORE TENSION — one tight paragraph, no analogy block ─
        media_setup = _story("media_host_setup")
        if not media_setup:
            media_setup = _story("what_actually_happened") or summary.why_it_matters.strip()
        parts.append(_clip_at_sentence(media_setup, 240))

        # ── ③ DYNAMIC DEBATE — voices ordered + clipped by composition rules ──
        if personas is not None:
            persona_map = {
                "business": personas.business,
                "linkedin": personas.linkedin,
                "genz":     personas.genz,
                "policy":   personas.policy,
            }

            active_pm = [
                (key, persona_map[key])
                for key in ordered_keys
                if key in persona_map
                and persona_map[key] is not None
                and persona_map[key].perspective
                and persona_map[key].perspective.strip()
            ]

            for key, p in active_pm:
                emoji, char_name = self._CHAR_DIALOGUE[key]
                raw_perspective = p.perspective.strip()

                # ── Deterministic banned-phrase gate ─────────────────────────
                # If the LLM produced a throat-clearing opener or banned inline
                # phrase, inject the BANNED_CONTENT sentinel.  OutputGuardrail
                # will block the post and the graph returns REGENERATE.
                banned_hit = _check_persona_text(raw_perspective)
                if banned_hit:
                    logger.warning(
                        "banned_phrase_detected persona=%s phrase='%s' — "
                        "injecting sentinel to force REGENERATE",
                        key, banned_hit,
                    )
                    # Sentinel is recognised by OutputGuardrail as BANNED_CONTENT
                    parts.append(
                        f"---\n\n{emoji} **{char_name}**\n\n"
                        f"[BANNED_CONTENT: persona={key} phrase='{banned_hit}']"
                    )
                    continue

                clipped = _clip_at_sentence(raw_perspective, voice_clip)
                parts.append(f"---\n\n{emoji} **{char_name}**\n\n\"{clipped}\"")

        # ── ④ SYNTHESIS — one punchy sentence, no fallback rambling ───────────
        host_synthesis = _story("media_host_synthesis") or _story("perspective")
        if not host_synthesis:
            host_synthesis = "The race to build AI faster is outpacing the ability to govern what gets built."
        parts.append(f"---\n\n🎙️ **THE AIFEEDERS QUESTION**\n\n{_clip_at_sentence(host_synthesis, 180)}")

        # ── ⑤ COMMENT TRIGGER — Forced-choice, forced-disagreement ───────────
        audience_cta = _story("media_host_audience_cta") or _story("future_question")
        if audience_cta and any(marker in audience_cta for marker in ["A —", "A -", "A)", "1️⃣", "1.", "🅰️"]):
            cta_body = audience_cta.strip()
        elif audience_cta:
            cta_body = (
                f"{_clip_at_sentence(audience_cta, 200)}\n\n"
                f"👇 Pick ONE. Then defend it against the strongest objection."
            )
        else:
            cta_body = (
                f"A) This genuinely changes how teams build.\n"
                f"B) It just creates more software nobody governs.\n"
                f"C) The real bottleneck shifts — creation is easy, accountability isn't.\n"
                f"D) Nothing changes until the ops cost of AI matches the hype.\n\n"
                f"👇 Pick ONE. Defend it."
            )
        parts.append(f"---\n\n💬 **YOUR TURN**\n\n{cta_body}")

        # ── ⑥ SOURCE LINK ────────────────────────────────────────────────────
        parts.append(f"Source → {summary.source_url}")

        # ── ⑦ HASHTAGS — appended last inside parts so they survive any clipping ─
        story_seo = []
        if isinstance(story, dict):
            story_seo = story.get("dynamic_seo_hashtags") or []
        elif story:
            story_seo = getattr(story, "dynamic_seo_hashtags", []) or []

        dynamic_tags = _extract_dynamic_tags(
            headline       = summary.headline,
            summary        = summary.summary,
            source         = summary.source,
            key_points     = summary.key_points,
            event_type     = event_type,
            story_seo_tags = story_seo,
            max_tags       = 4,
        )
        tag_line = " ".join(dynamic_tags) if dynamic_tags else self._BASE_HASHTAGS
        parts.append(tag_line)

        # ── Footer (brand + attribution — no hashtags here) ───────────────────
        # Join body (including hashtags) first, then append the small footer.
        body = "\n\n".join(p for p in parts if p)
        footer_block = self._FOOTER_BASE

        separator = "\n\n"
        full_post = body + separator + footer_block

        # Single clip pass — body already short enough that footer is always safe
        if _linkedin_len(full_post) > POST_LIMIT:
            # Clip the body only, keep footer intact
            max_body = POST_LIMIT - _linkedin_len(separator) - _linkedin_len(footer_block)
            body = _clip_at_sentence(body, max_body)
            full_post = body + separator + footer_block

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
