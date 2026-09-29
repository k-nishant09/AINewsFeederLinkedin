# skills.md — PublisherAgent

**File:** [`publisher_agent.py`](publisher_agent.py)
**Stage:** 13 — PUBLISH
**LLM:** None — deterministic only (no LLM calls in this agent)

---

## Purpose

The only component that calls the LinkedIn MCP. Assembles the full post from structured agent outputs and publishes it. All decisions are deterministic — no LLM generation here.

---

## Tools Called

| Tool | Call | Purpose |
|---|---|---|
| `LinkedInMCPClient` | `create_post(text, publication_key)` | Publishes the main post (HTTP 201 → `post_urn`) |
| `LinkedInMCPClient` | `create_comment(post_urn, text, comment_key)` | Posts one comment per active persona (sequential) |
| `GrammarAgent` | `correct(post_text)` | Final proofread before publish |
| `ComicGenerator` | `generate_comic(script)` | SVG → PNG 6-panel debate strip attached as image |
| `PublishedStore` | `mark_published(article_id)` | Prevents re-publishing same article |

---

## Post Structure

```
[Hook line — article-specific, event-type + sentiment derived]
[Context line — editorial weight of this story]
[Intelligence line — novelty/velocity signal, when strong enough]
[Content angle line — Jev's missing_angle, when present]

[Source → URL]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💼 FOUNDER  · 🧑‍💻 ENGINEER  · ⚖️ SKEPTIC  · 🏛️ POLICY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[CTA — numbered choices, article-specific, event-type derived]

🤖 AIFeeders · Daily AI Intelligence · Powered by Jev
#AI #tag1 #tag2 …
```

Comments: each persona's full `perspective` posted sequentially as a LinkedIn comment under the main post.

---

## Key Functions

| Function | Purpose |
|---|---|
| `_build_hook_line()` | 5-variant rotation per event_type+sentiment, headline-hash slot selection |
| `_build_cta()` | Numbered-choice question derived from event_type + intelligence signals |
| `_build_context_line()` | Editorial weight line (significance + relevance + event_type) |
| `_extract_dynamic_tags()` | Dynamic SEO hashtags — zero static company names hardcoded |
| `_to_unicode_bold()` | Converts `**bold**` markdown to Unicode Mathematical Bold (LinkedIn renders natively) |
| `_linkedin_len()` | UTF-16 character counting (emoji = 2 units) matching LinkedIn's actual limit |
| `_check_persona_text()` | Banned-phrase scanner shared with EvaluationAgent pre-scan |

---

## Banned Phrase Lists

`_BANNED_OPENERS` — anecdote and hypothetical openers that throat-clear before the point.
`_BANNED_INLINE_PHRASES` — AI clichés, lazy abstractions, generic closers.
`CANONICAL_BANNED_PHRASES` — public alias consumed by `PersonaAgent` for retry injection.
`_REAL_ABSTRACTION_PATTERN`, `_MARKS_SIGNIFICANT_PATTERN`, `_FIRST_MAJOR_ISSUE_PATTERN` — regex catch-alls.

---

## Design Decisions

- **No LLM calls** — the publisher is a pure transformation layer. All text was generated upstream.
- **Comments are sequential**, never `gather()` — LinkedIn's API enforces ordering; parallel posts arrive out of order.
- **`PUBLISHING_ENABLED=false`** — complete LinkedIn bypass for smoke tests. Returns mock URNs.
- **Unicode bold** instead of `**markdown**` — LinkedIn API renders asterisks as literal characters.
- **`_repaired_post` key** — if `ReachScoreAgent` auto-repaired the post, `persona_dict["_repaired_post"]` is used directly, skipping re-composition.
