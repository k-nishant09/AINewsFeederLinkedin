"""
linkedin_skills_optimizer.py
=============================
Post-generation LinkedIn optimization layer for AIFeeders.

Applies three passes from the linkedin-skills methodology to the assembled
post BEFORE it reaches the evaluation judge:

  Pass 1 — HOOK SELECTOR
    Scores the current hook against 2026 formula heuristics and optionally
    generates up to 3 alternative hook candidates (number-first, contrarian,
    cost-tension). Selects the strongest one.

  Pass 2 — HUMANIZER
    Scrubs AI-vocabulary density, reveal bridges ("The result?", "Here's what"),
    stacked triads, performed-sincerity patterns, and staccato fragment runs.
    Preserves AIFeeders identity: persona names, debate structure, factual claims,
    source attribution, and comic caption are NEVER touched.

  Pass 3 — AUDIT
    Scores the optimized post on: hook strength, commentability, AI-style
    density, CTA quality, and LinkedIn 2026 algorithm compliance.
    Returns a structured AuditResult that feeds into the evaluation judge.

Contract:
  - Does NOT change: factual news context, source evidence, 6-scene structure,
    persona identities, persona-to-persona relationships, practical scenarios,
    Media Host synthesis.
  - MAY optimize: opening hook, caption structure, sentence length, readability,
    CTA framing, question specificity, hashtag selection, AI-vocabulary density.

Architecture position:
  generate_personas → [linkedin_skills_optimizer] → evaluate → publish
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from daily_news.config.llm_factory import make_llm
from daily_news.config.settings import get_settings

logger = logging.getLogger(__name__)

# ── 2026 AI-vocabulary density list (from linkedin-humanizer V3) ──────────────
_AI_VOCAB: tuple[str, ...] = (
    "leverage", "streamline", "harness", "delve", "unlock", "foster",
    "fundamentally", "significant", "crucial", "notably", "particularly",
    "comprehensive", "robust", "landscape", "nuanced", "multifaceted",
    "holistic", "elevate", "empower", "transformative", "game-changer",
    "game-changing", "deep dive", "cutting-edge", "revolutionary",
    "paradigm", "realm", "tapestry", "intricate", "journey",
    # 2026 LinkedIn-specific slop layer
    "quietly", "matters", "built different", "doing the heavy lifting",
    "let that sink in", "that's the real story", "load-bearing",
)

# ── Reveal bridges that are reach-negative in 2026 ───────────────────────────
_REVEAL_BRIDGES: tuple[str, ...] = (
    "the result?",
    "here's what",
    "here's how",
    "plot twist:",
    "stop x, start",
    "it's not x, it's",
    "the result is",
)

# ── Hook formula codes best suited for AIFeeders debate format ────────────────
# F7  Odd-Precision / number-first  → strongest 2026 opener (+34% median likes)
# F10 Contrarian + historical       → comments-optimised
# F18 False-binary dissolve         → comments/reposts, exactly ONE contrast
# F2  R.I.P. Obituary               → era-ending / pivot stories (reposts)
_HOOK_FORMULAS = {
    "F7":  "Number-first (odd-precision stat or cost figure in line 1)",
    "F10": "Contrarian + historical receipt (challenge a sacred cow with a dated fact)",
    "F18": "False-binary dissolve (both obvious answers fail — one sharp contrast only)",
    "F2":  "R.I.P. Obituary (era-ending claim about a category or assumption)",
}


@dataclass
class AuditResult:
    """Structured audit scores returned to the evaluation judge."""
    hook_strength: float          # 0.0–1.0  (≥0.7 = good)
    commentability: float         # 0.0–1.0  (≥0.7 = good)
    ai_style_density: float       # 0.0–1.0  (≤0.25 = good, >0.5 = bad)
    cta_quality: float            # 0.0–1.0  (≥0.7 = good)
    algorithm_compliance: float   # 0.0–1.0  (≥0.7 = good)
    overall: float                # weighted composite
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hook_formula_used: str = ""
    optimized_hook: str = ""


@dataclass
class OptimizedPost:
    """Result of the full 3-pass optimization."""
    post_text: str           # post text after hook + humanizer passes
    audit: AuditResult
    hook_was_replaced: bool = False
    humanizer_changes: list[str] = field(default_factory=list)


class LinkedInSkillsOptimizer:
    """
    Applies linkedin-skills methodology as a post-processing optimization layer.
    Called after persona generation, before the evaluation judge.
    """

    def __init__(self) -> None:
        s = get_settings()
        if not s.llm_base_url:
            raise RuntimeError("LLM_BASE_URL not set — LinkedIn optimizer requires LLM")

        # Small pool — optimizer runs once per article, not in parallel bursts
        self._llm = make_llm(
            temperature=0.3,   # slightly creative for hook variants
            settings=s,
            max_connections=5,
            max_keepalive=5,
            keepalive_expiry=30.0,
        )
        self._judge_llm = make_llm(
            temperature=0.0,   # deterministic audit
            settings=s,
            max_connections=5,
            max_keepalive=5,
            keepalive_expiry=30.0,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def optimize(
        self,
        post_text: str,
        headline: str,
        central_tension: str,
        story_style: str,
        run_id: str = "",
    ) -> OptimizedPost:
        """
        Run all 3 passes. Returns OptimizedPost with optimized text + audit scores.
        Falls back gracefully — if any pass fails, returns the original post with
        a partial audit so the pipeline is never blocked by the optimizer.
        """
        try:
            # Pass 1: Hook selection
            post_after_hook, hook_replaced, hook_formula = await self._pass1_hook(
                post_text, headline, central_tension, story_style, run_id
            )
        except Exception as exc:
            logger.warning("[%s] LinkedInOptimizer pass1 hook failed: %s", run_id, exc)
            post_after_hook, hook_replaced, hook_formula = post_text, False, ""

        try:
            # Pass 2: Humanizer
            post_after_human, changes = self._pass2_humanizer(post_after_hook)
        except Exception as exc:
            logger.warning("[%s] LinkedInOptimizer pass2 humanizer failed: %s", run_id, exc)
            post_after_human, changes = post_after_hook, []

        try:
            # Pass 3: Audit
            audit = await self._pass3_audit(post_after_human, run_id)
            audit.hook_formula_used = hook_formula
        except Exception as exc:
            logger.warning("[%s] LinkedInOptimizer pass3 audit failed: %s", run_id, exc)
            audit = self._fallback_audit(post_after_human)
            audit.hook_formula_used = hook_formula

        if hook_replaced or changes:
            logger.info(
                "[%s] LinkedInOptimizer: hook_replaced=%s humanizer_changes=%d "
                "hook_strength=%.2f commentability=%.2f ai_density=%.2f overall=%.2f",
                run_id, hook_replaced, len(changes),
                audit.hook_strength, audit.commentability,
                audit.ai_style_density, audit.overall,
            )

        return OptimizedPost(
            post_text=post_after_human,
            audit=audit,
            hook_was_replaced=hook_replaced,
            humanizer_changes=changes,
        )

    # ── Pass 1: Hook Selector ─────────────────────────────────────────────────

    async def _pass1_hook(
        self,
        post_text: str,
        headline: str,
        central_tension: str,
        story_style: str,
        run_id: str,
    ) -> tuple[str, bool, str]:
        """
        Extracts the current hook (first bold line), scores it, and if weak
        generates up to 3 alternatives, selects the strongest, and replaces.
        Returns (post_text, was_replaced, formula_code).
        """
        current_hook = self._extract_hook(post_text)
        hook_score = self._score_hook_heuristic(current_hook)

        # If hook is already strong (number-first or contrarian structure), keep it
        if hook_score >= 0.72:
            formula = self._detect_formula(current_hook)
            return post_text, False, formula

        # Pick the best formula for this story style
        formula = self._pick_formula(story_style)

        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are a LinkedIn post hook specialist using 2026 formula heuristics.\n\n"
             "Rules that ALWAYS apply:\n"
             "- NEVER open with a question (-34% median likes in 2026)\n"
             "- PREFER number-first in line 1 (+34% median likes)\n"
             "- NEVER use: 'Here's what', 'Stop X start Y', 'The result?', 'Plot twist:'\n"
             "- NEVER use AI vocabulary: leverage, streamline, game-changer, fundamentally\n"
             "- Max 210 characters (LinkedIn 'see more' fold)\n"
             "- Must be a declarative statement of specific documented fact or structural tension\n"
             "- DO NOT invent numbers or facts not present in the headline/tension\n\n"
             f"Formula to use: {_HOOK_FORMULAS.get(formula, 'Number-first contrarian fact')}\n\n"
             "Return ONLY the hook line. No quotes, no explanation, no prefix."),
            ("human",
             f"Headline: {headline}\n"
             f"Central tension: {central_tension}\n"
             f"Current hook (weak): {current_hook}\n\n"
             "Write a stronger hook using the formula above."),
        ])

        chain = prompt | self._llm
        result = await chain.ainvoke({})
        new_hook = result.content.strip().strip('"').strip("'")

        # Validate — must be stronger than current
        if not new_hook or self._score_hook_heuristic(new_hook) <= hook_score:
            return post_text, False, formula

        # Replace the hook line in the post (the 🚨 **...** line)
        new_post = re.sub(
            r"🚨 \*\*(.+?)\*\*",
            f"🚨 **{new_hook}**",
            post_text,
            count=1,
            flags=re.DOTALL,
        )
        logger.info(
            "[%s] hook replaced formula=%s score %.2f→%.2f: %s",
            run_id, formula, hook_score,
            self._score_hook_heuristic(new_hook),
            new_hook[:80],
        )
        return new_post, True, formula

    def _extract_hook(self, post_text: str) -> str:
        m = re.search(r"🚨 \*\*(.+?)\*\*", post_text, re.DOTALL)
        return m.group(1).strip() if m else post_text[:210]

    def _score_hook_heuristic(self, hook: str) -> float:
        """
        Fast deterministic score 0.0–1.0 based on 2026 linkedin-skills heuristics.
        No LLM call — used to decide whether to invoke the hook rewriter.
        """
        score = 0.5
        h = hook.lower().strip()

        # +0.2 number-first (strongest 2026 signal)
        if re.match(r"^\d", hook.strip()) or re.search(r"\b\d[\d,\.]+[x%$]?\b", hook[:60]):
            score += 0.20

        # +0.15 contrarian signal word
        if any(w in h for w in ("not", "isn't", "doesn't", "fails", "wrong", "harder", "worse", "cheaper", "broken")):
            score += 0.15

        # +0.10 named entity (company / proper noun)
        if re.search(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", hook):
            score += 0.10

        # -0.30 question opener (biggest 2026 negative)
        if h.endswith("?") or h.startswith(("why ", "what ", "how ", "when ", "is ", "are ", "can ", "could ", "should ", "do ", "does ", "did ", "will ", "would ")):
            score -= 0.30

        # -0.20 AI-vocab tells
        ai_hits = sum(1 for w in _AI_VOCAB if w in h)
        score -= min(ai_hits * 0.10, 0.20)

        # -0.15 generic opener patterns
        if any(h.startswith(p) for p in ("ai is ", "this is ", "today ", "in a recent", "x just ", "here's what", "the future of")):
            score -= 0.15

        return max(0.0, min(1.0, score))

    def _detect_formula(self, hook: str) -> str:
        h = hook.lower()
        if re.match(r"^\d", hook.strip()):
            return "F7"
        if any(w in h for w in ("not ", "isn't", "fails", "wrong", "harder")):
            return "F10"
        if "both" in h or "neither" in h:
            return "F18"
        return "F7"

    def _pick_formula(self, story_style: str) -> str:
        mapping = {
            "ai_debate":          "F10",
            "investor_dilemma":   "F7",
            "uncomfortable_truth": "F10",
            "architecture_battle": "F18",
            "ai_postmortem":      "F2",
            "prediction_bet":     "F18",
            "diagram_breakdown":  "F7",
        }
        return mapping.get(story_style, "F7")

    # ── Pass 2: Humanizer ─────────────────────────────────────────────────────

    def _pass2_humanizer(self, post_text: str) -> tuple[str, list[str]]:
        """
        Deterministic scrub pass — no LLM call.
        Applies linkedin-humanizer V3 STRICT tier rules inline.
        NEVER touches: persona names, debate structure labels, factual claims,
        source URLs, hashtags, the comic caption, or the header.
        """
        changes: list[str] = []
        text = post_text

        # 1. Remove reveal bridges (reach-negative)
        for bridge in _REVEAL_BRIDGES:
            pattern = re.compile(re.escape(bridge), re.IGNORECASE)
            if pattern.search(text):
                text = pattern.sub("", text)
                changes.append(f"removed reveal bridge: '{bridge}'")

        # 2. AI vocabulary density — only replace when ≥3 hits in a paragraph
        paragraphs = text.split("\n\n")
        new_paragraphs = []
        for para in paragraphs:
            # Skip structural lines (headers, persona labels, source, hashtags)
            if any(marker in para for marker in [
                "AIFEEDERS", "**THE AIFEEDERS", "**YOUR TURN", "**THE AIFEEDERS QUESTION",
                "Source →", "#", "🧠", "🚨", "🎙️", "💬", "🤖", "---",
            ]):
                new_paragraphs.append(para)
                continue
            hits = [w for w in _AI_VOCAB if w.lower() in para.lower()]
            if len(hits) >= 3:
                # Replace the most egregious duplicates (keep first occurrence)
                seen: set[str] = set()
                for hit in hits[1:]:  # leave first occurrence
                    if hit.lower() not in seen:
                        para = re.sub(
                            r"\b" + re.escape(hit) + r"\b", "", para,
                            count=1, flags=re.IGNORECASE
                        ).strip()
                        seen.add(hit.lower())
                        changes.append(f"ai-vocab density: removed duplicate '{hit}'")
            new_paragraphs.append(para)
        text = "\n\n".join(new_paragraphs)

        # 3. Cap staccato fragment runs — at most 2 standalone fragments per post
        # A standalone fragment is a line of ≤6 words ending without punctuation
        lines = text.split("\n")
        fragment_count = 0
        new_lines = []
        for line in lines:
            stripped = line.strip()
            word_count = len(stripped.split())
            is_fragment = (
                1 < word_count <= 6
                and not stripped.endswith((".", "!", "?", ":", "—", "→"))
                and not stripped.startswith(("---", "🧠", "🚨", "🎙️", "💬", "🤖", "#", "Source"))
                and not re.match(r"^[A-D]\)", stripped)  # keep A/B/C/D CTA options
            )
            if is_fragment:
                fragment_count += 1
                if fragment_count > 2:
                    changes.append(f"staccato cap: merged fragment '{stripped[:40]}'")
                    # Merge with previous non-empty line
                    for i in range(len(new_lines) - 1, -1, -1):
                        if new_lines[i].strip():
                            new_lines[i] = new_lines[i].rstrip(" .") + ", " + stripped.lower() + "."
                            line = ""
                            break
            new_lines.append(line)
        text = "\n".join(new_lines)

        # 4. Remove "What do you think?" and "Tag someone" engagement bait closers
        bait_patterns = [
            r"what do you think\??\s*",
            r"tag someone who needs (to see |to hear |this)",
            r"drop (your|a) (thoughts?|comment|take) below[.!]?\s*",
        ]
        for bp in bait_patterns:
            m = re.search(bp, text, re.IGNORECASE)
            if m:
                text = text[:m.start()] + text[m.end():]
                changes.append(f"removed engagement bait: '{m.group(0).strip()[:40]}'")

        # Clean up any double blank lines introduced
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text, changes

    # ── Pass 3: Audit ─────────────────────────────────────────────────────────

    async def _pass3_audit(self, post_text: str, run_id: str) -> AuditResult:
        """LLM-backed audit returning structured scores for the evaluation judge."""
        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are a LinkedIn algorithm and engagement auditor for 2026.\n"
             "Evaluate the post strictly. Return ONLY valid JSON, no markdown fences.\n\n"
             "Score each dimension 0.0–1.0:\n"
             "- hook_strength: Does line 1 use a number, named entity, or structural tension "
             "(not a question, not a generic AI phrase)? ≥0.7 = good.\n"
             "- commentability: Does the CTA ask ONE concrete, specific, answerable question "
             "that forces a genuine position? Is it derived from the story tension? ≥0.7 = good.\n"
             "- ai_style_density: Fraction of paragraphs containing ≥3 AI-vocabulary markers "
             "(leverage, streamline, fundamentally, etc.). ≤0.25 = good, >0.5 = bad.\n"
             "- cta_quality: Is the forced-choice (A/B/C/D) derived from the story's specific "
             "unresolved tension, or is it generic boilerplate? ≥0.7 = good.\n"
             "- algorithm_compliance: 2026 rules — hook in first 210 chars, no external links "
             "in body, ≤4 hashtags, closing question present, 900-1900 chars. ≥0.7 = good.\n\n"
             "Also list:\n"
             "- blockers: list of strings (issues that would significantly hurt reach)\n"
             "- warnings: list of strings (minor improvements)\n\n"
             "Return JSON:\n"
             '{{"hook_strength":0.0,"commentability":0.0,"ai_style_density":0.0,'
             '"cta_quality":0.0,"algorithm_compliance":0.0,"blockers":[],"warnings":[]}}'),
            ("human", "Post to audit:\n\n{post_text}"),
        ])

        chain = prompt | self._judge_llm
        result = await chain.ainvoke({"post_text": post_text[:3000]})
        raw = result.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        import json
        data = json.loads(raw)

        hs  = float(data.get("hook_strength", 0.5))
        cm  = float(data.get("commentability", 0.5))
        asd = float(data.get("ai_style_density", 0.3))
        cq  = float(data.get("cta_quality", 0.5))
        ac  = float(data.get("algorithm_compliance", 0.7))

        # Weighted composite — AIFeeders priority: commentability > hook > CTA > density > algo
        overall = (cm * 0.30) + (hs * 0.25) + (cq * 0.20) + ((1.0 - asd) * 0.15) + (ac * 0.10)

        return AuditResult(
            hook_strength=hs,
            commentability=cm,
            ai_style_density=asd,
            cta_quality=cq,
            algorithm_compliance=ac,
            overall=overall,
            blockers=data.get("blockers", []),
            warnings=data.get("warnings", []),
        )

    def _fallback_audit(self, post_text: str) -> AuditResult:
        """Fast heuristic audit when LLM audit fails."""
        hook = self._extract_hook(post_text)
        hs   = self._score_hook_heuristic(hook)
        has_abcd = bool(re.search(r"[A-D]\)", post_text))
        has_question = "?" in post_text
        char_count = len(post_text)
        ai_hits = sum(1 for w in _AI_VOCAB if w.lower() in post_text.lower())
        ai_density = min(ai_hits / max(len(post_text.split("\n\n")), 1) / 3.0, 1.0)
        cm = 0.8 if has_abcd and has_question else (0.6 if has_question else 0.4)
        ac = 0.8 if 900 <= char_count <= 2000 else 0.5
        overall = (cm * 0.30) + (hs * 0.25) + (0.6 * 0.20) + ((1.0 - ai_density) * 0.15) + (ac * 0.10)
        return AuditResult(
            hook_strength=hs, commentability=cm, ai_style_density=ai_density,
            cta_quality=0.6, algorithm_compliance=ac, overall=overall,
        )
