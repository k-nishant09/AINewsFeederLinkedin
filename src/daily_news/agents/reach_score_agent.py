"""
Reach Score Agent — pre-publish LinkedIn reach optimiser.
==========================================================

Scores a composed post on 6 organic-reach dimensions *before* it is sent to
LinkedIn. No LLM calls — pure deterministic text analysis using stdlib only.
Runs in < 1 ms per post.

Scoring dimensions (each 0–10, weighted sum → 0–100):

  1. hook_strength      (20 pts) — first 2 lines before "see more" cutoff
  2. specificity        (20 pts) — concrete numbers, named entities, original take
  3. question_quality   (20 pts) — closing question invites substantive reply
  4. length_fit         (15 pts) — 150–300 words is optimal for text posts
  5. bait_penalty       (15 pts) — penalise "comment X", excess hashtags, engagement-bait
  6. topic_coherence    (10 pts) — post stays on AI/tech beat (known entity check)

Gate:
  score ≥ REACH_THRESHOLD  → "PUBLISH"  (proceed)
  score <  REACH_THRESHOLD → "REVISE"   (publisher auto-applies repairs)

Auto-repairs applied on REVISE (no LLM needed):
  - Strip hashtags down to 3 max
  - Drop lines containing engagement-bait patterns
  - Trim post to word-count sweet spot if too long

Usage inside the graph:
  post_text = publisher_agent._compose_main_post(...)
  score     = ReachScoreAgent().score(post_text)
  if score.verdict == "REVISE":
      post_text = ReachScoreAgent.repair(post_text)
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Literal

# ── Thresholds ────────────────────────────────────────────────────────────────

REACH_THRESHOLD = 55   # below this → REVISE

# First N characters that appear before LinkedIn's "see more" fold (~220 chars)
HOOK_VISIBLE_CHARS = 220

# Word-count sweet spot for text posts (LinkedIn algorithm rewards this range)
WORD_COUNT_MIN = 150
WORD_COUNT_MAX = 300

# Max hashtags before penalty kicks in (LinkedIn recommends 0–3)
MAX_HASHTAGS = 3

# Known AI/tech domain signals — post must mention ≥ 1 for full topic coherence
_AI_DOMAIN_SIGNALS = {
    "ai", "artificial intelligence", "machine learning", "llm", "gpt", "model",
    "openai", "anthropic", "google", "microsoft", "nvidia", "deepmind",
    "agent", "automation", "regulation", "policy", "enterprise", "startup",
    "funding", "acquisition", "chip", "semiconductor", "generative",
    "workforce", "reskilling", "deployment", "inference", "training",
}

# Engagement-bait patterns — phrases LinkedIn's algorithm now down-ranks
_BAIT_PATTERNS = [
    r'\bcomment\s+["\']?[A-Z0-9]{1,4}["\']?\s+(?:if|to|below)',  # "comment YES if"
    r'\blike\s+(?:if|this)\b',                                     # "like if you agree"
    r'\brepost\s+(?:if|this)\b',
    r'\bfollow\s+(?:me|us)\s+for\b',
    r'\bsave\s+this\s+post\b',
    r'\bagree\s+or\s+disagree\b',
    r'\bwhat\s+do\s+you\s+think\??$',                              # "what do you think?" alone
    r'\byes\s+or\s+no\b',
    r'\bshare\s+this\b',
]

# Strong hook openers — phrases that correlate with high dwell time
_HOOK_STRENGTH_SIGNALS = [
    r'\beveryone\s+is\s+talking\b',
    r'\bhere.s\s+the\s+(?:real|hard|honest|production|uncomfortable)',
    r'\bthe\s+(?:real|hard|honest|uncomfortable|inconvenient)\s+truth',
    r'\bno\s+one\s+is\s+asking\b',
    r'\bnobody\s+is\s+asking\b',
    r'\bstop\s+me\s+if\b',
    r'\bi\s+keep\s+(?:coming\s+back|thinking)\b',
    r'\bwhat\s+(?:actually|really)\s+(?:happens|matters|works)\b',
    r'\bhere.s\s+what\s+(?:nobody|no\s+one)\b',
    r'\bdifferent\s+question\b',
    r'\bproduction\s+(?:question|reality|problem|challenge)\b',
]

# Weak hook patterns — generic throat-clearing
_WEAK_HOOK_PATTERNS = [
    r'^(?:in\s+(?:a\s+)?(?:move|step)|as\s+ai\s+continues)',
    r'^(?:artificial\s+intelligence|machine\s+learning)\s+is\s+(?:rapidly|quickly|fast)',
    r'^(?:ai|technology)\s+(?:has|is)\s+(?:been|become)\s+an?\s+',
    r'^(?:today|recently),?\s+(?:a\s+major|an?\s+important)',
    r'^it\s+(?:has\s+)?(?:been\s+)?(?:announced|reported)\s+that',
]

# Closing question signals — markers of a high-quality CTA
_GOOD_QUESTION_SIGNALS = [
    r'\b(?:1️⃣|2️⃣|3️⃣|4️⃣|5️⃣|6️⃣)',  # numbered-choice format
    r'\bdrop\s+(?:the\s+number|your|it|a\s+comment)',
    r'\bwhat.s\s+(?:your|the)\s+(?:biggest|main|top|real)',
    r'\bfor\s+those\s+(?:already\s+)?(?:working|building|deploying|using)',
    r'\bif\s+you\s+(?:were|are)\s+(?:evaluating|building|deploying)',
    r'\bgenuinely\s+curious\b',
    r'\bwhere\s+do\s+you\s+land\b',
    r'\bdrop\s+(?:your|a|the)',
]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ReachScore:
    """Result of scoring a single composed post."""

    hook_strength:    float   # 0–20
    specificity:      float   # 0–20
    question_quality: float   # 0–20
    length_fit:       float   # 0–15
    bait_penalty:     float   # 0–15  (higher = *fewer* bait signals = good)
    topic_coherence:  float   # 0–10

    total: float = field(init=False)
    verdict: Literal["PUBLISH", "REVISE"] = field(init=False)

    # Diagnostic detail
    word_count:    int   = 0
    hashtag_count: int   = 0
    bait_hits:     list[str] = field(default_factory=list)
    notes:         list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.total   = round(
            self.hook_strength + self.specificity + self.question_quality
            + self.length_fit + self.bait_penalty + self.topic_coherence,
            1,
        )
        self.verdict = "PUBLISH" if self.total >= REACH_THRESHOLD else "REVISE"

    def summary_line(self) -> str:
        return (
            f"reach_score={self.total:.0f}/100 verdict={self.verdict} "
            f"hook={self.hook_strength:.0f} spec={self.specificity:.0f} "
            f"q={self.question_quality:.0f} len={self.length_fit:.0f} "
            f"bait={self.bait_penalty:.0f} topic={self.topic_coherence:.0f} "
            f"words={self.word_count} hashtags={self.hashtag_count}"
        )


# ── Scoring engine ────────────────────────────────────────────────────────────

class ReachScoreAgent:
    """
    Deterministic pre-publish reach scorer.
    All methods are pure functions — no state, no I/O.
    """

    # ── 1. Hook strength (0–20) ───────────────────────────────────────────────

    @staticmethod
    def _score_hook(text: str) -> tuple[float, list[str]]:
        """
        Score the first HOOK_VISIBLE_CHARS of the post.

        +20 if strong hook signal found
        +10 if hook visible-window is non-empty and not a weak opener
         -5 for each weak hook pattern matched (floor 0)
        """
        hook = text[:HOOK_VISIBLE_CHARS].lower()
        notes: list[str] = []

        strong_matches = sum(
            1 for pat in _HOOK_STRENGTH_SIGNALS if re.search(pat, hook, re.IGNORECASE)
        )
        weak_matches = sum(
            1 for pat in _WEAK_HOOK_PATTERNS if re.search(pat, hook, re.IGNORECASE)
        )

        if strong_matches >= 1:
            score = 20.0
            notes.append(f"hook: {strong_matches} strong signal(s) in opener")
        elif hook.strip():
            score = max(0.0, 12.0 - weak_matches * 5)
            if weak_matches:
                notes.append(f"hook: {weak_matches} weak opener pattern(s) — consider rewriting")
            else:
                notes.append("hook: neutral opener — no strong/weak signals")
        else:
            score = 0.0
            notes.append("hook: empty opener")

        return score, notes

    # ── 2. Specificity (0–20) ─────────────────────────────────────────────────

    @staticmethod
    def _score_specificity(text: str) -> tuple[float, list[str]]:
        """
        Award points for concrete, specific content:
          +6  at least one number/percentage/dollar amount
          +6  at least one named entity (Capitalised word that isn't a sentence start)
          +4  source attribution present ("→ https://..." or "Source: ...")
          +4  contains at least one bulleted fact (▸ or •)
        """
        score = 0.0
        notes: list[str] = []

        # Numbers and quantities
        num_matches = re.findall(r'\b\d[\d,.%$Mm]|\b\d+[kK]\b', text)
        if num_matches:
            score += 6.0
            notes.append(f"specificity: {len(num_matches)} numeric value(s) found")
        else:
            notes.append("specificity: no numeric values — add a stat or figure")

        # Named entities (capitalised mid-sentence words, excluding pure upper-case acronyms)
        words = text.split()
        named = [
            w for w in words
            if w and w[0].isupper() and not w.isupper() and len(w) > 2
            and w.strip(string.punctuation) not in {"The", "A", "An", "In", "For", "At", "On"}
        ]
        if len(named) >= 2:
            score += 6.0
        elif named:
            score += 3.0
        else:
            notes.append("specificity: no named entities found")

        # Source link
        if re.search(r'https?://', text) or re.search(r'Full story\s*→', text, re.IGNORECASE):
            score += 4.0
        else:
            notes.append("specificity: no source link in post body")

        # Bulleted facts
        if re.search(r'^[▸•]\s', text, re.MULTILINE):
            score += 4.0
        else:
            notes.append("specificity: no bulleted facts (▸ or •)")

        return min(score, 20.0), notes

    # ── 3. Question quality (0–20) ────────────────────────────────────────────

    @staticmethod
    def _score_question(text: str) -> tuple[float, list[str]]:
        """
        Score the closing CTA question quality.
        Looks at the last 400 chars where the CTA lives.
        """
        tail = text[-400:].lower()
        notes: list[str] = []

        good_hits = sum(
            1 for pat in _GOOD_QUESTION_SIGNALS if re.search(pat, tail, re.IGNORECASE)
        )

        # Penalty: plain "what do you think?" or yes/no question at end
        plain_cta = bool(re.search(r'what\s+do\s+you\s+think\s*\??\s*$', tail))
        yes_no    = bool(re.search(r'\byes\s+or\s+no\b', tail))

        if good_hits >= 2:
            score = 20.0
            notes.append(f"question: {good_hits} quality CTA signals (numbered choice or specific invite)")
        elif good_hits == 1:
            score = 14.0
            notes.append("question: 1 quality CTA signal — consider numbered choices")
        elif plain_cta or yes_no:
            score = 4.0
            notes.append("question: generic/yes-no CTA — rewrite with specific numbered options")
        else:
            score = 8.0
            notes.append("question: no strong CTA detected in closing section")

        return score, notes

    # ── 4. Length fit (0–15) ──────────────────────────────────────────────────

    @staticmethod
    def _score_length(text: str) -> tuple[float, int, list[str]]:
        """
        LinkedIn text posts perform best at 150–300 words.
        Outside that range apply a sliding penalty.
        """
        # Count words — exclude hashtag tokens
        clean = re.sub(r'#\w+', '', text)
        words = [w for w in clean.split() if w.strip(string.punctuation)]
        wc = len(words)
        notes: list[str] = []

        if WORD_COUNT_MIN <= wc <= WORD_COUNT_MAX:
            score = 15.0
        elif wc < WORD_COUNT_MIN:
            # Too short — lose 1 pt per 10 words under the floor
            under = WORD_COUNT_MIN - wc
            score = max(0.0, 15.0 - (under // 10))
            notes.append(f"length: {wc} words — below {WORD_COUNT_MIN} optimum (too short)")
        else:
            # Too long — lose 1 pt per 15 words over the ceiling
            over = wc - WORD_COUNT_MAX
            score = max(0.0, 15.0 - (over // 15))
            notes.append(f"length: {wc} words — above {WORD_COUNT_MAX} optimum (may be trimmed)")

        return score, wc, notes

    # ── 5. Bait penalty (0–15) ────────────────────────────────────────────────

    @staticmethod
    def _score_bait(text: str) -> tuple[float, int, list[str], list[str]]:
        """
        Penalise engagement-bait patterns and excess hashtags.
        Full score (15) = no bait. -3 per bait hit. -1 per excess hashtag.
        """
        hashtags = re.findall(r'#\w+', text)
        ht_count = len(hashtags)
        bait_hits: list[str] = []
        notes: list[str] = []

        for pat in _BAIT_PATTERNS:
            m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            if m:
                bait_hits.append(m.group(0)[:60])

        excess_ht = max(0, ht_count - MAX_HASHTAGS)
        score = 15.0 - (len(bait_hits) * 3) - excess_ht
        score = max(0.0, score)

        if bait_hits:
            notes.append(f"bait: {len(bait_hits)} engagement-bait pattern(s): {bait_hits[:2]}")
        if excess_ht:
            notes.append(f"bait: {ht_count} hashtags — {excess_ht} over the {MAX_HASHTAGS}-tag cap")

        return score, ht_count, bait_hits, notes

    # ── 6. Topic coherence (0–10) ─────────────────────────────────────────────

    @staticmethod
    def _score_topic(text: str) -> tuple[float, list[str]]:
        """
        Check post stays on the AI/tech beat.
        ≥ 3 domain signals → 10.  2 → 7.  1 → 4.  0 → 0.
        """
        lower = text.lower()
        hits = sum(1 for sig in _AI_DOMAIN_SIGNALS if re.search(r'\b' + re.escape(sig) + r'\b', lower))
        notes: list[str] = []

        if hits >= 3:
            score = 10.0
        elif hits == 2:
            score = 7.0
        elif hits == 1:
            score = 4.0
        else:
            score = 0.0
            notes.append("topic: no AI/tech domain signals found — off-beat post?")

        return score, notes

    # ── Public API ────────────────────────────────────────────────────────────

    def score(self, post_text: str) -> ReachScore:
        """Score a post and return a ReachScore dataclass."""
        hook,   hook_notes   = self._score_hook(post_text)
        spec,   spec_notes   = self._score_specificity(post_text)
        qstn,   qstn_notes   = self._score_question(post_text)
        lfit,   wc, len_notes = self._score_length(post_text)
        bait,   ht, bait_hits, bait_notes = self._score_bait(post_text)
        topic,  topic_notes  = self._score_topic(post_text)

        all_notes = hook_notes + spec_notes + qstn_notes + len_notes + bait_notes + topic_notes

        return ReachScore(
            hook_strength    = hook,
            specificity      = spec,
            question_quality = qstn,
            length_fit       = lfit,
            bait_penalty     = bait,
            topic_coherence  = topic,
            word_count       = wc,
            hashtag_count    = ht,
            bait_hits        = bait_hits,
            notes            = all_notes,
        )

    # ── Auto-repair ───────────────────────────────────────────────────────────

    @staticmethod
    def repair(post_text: str) -> str:
        """
        Apply deterministic repairs to a post that scored below REACH_THRESHOLD.

        Repairs (in order):
          1. Strip hashtag count to MAX_HASHTAGS (keep last N — footer hashtags
             are more topical than base tags)
          2. Remove lines that contain engagement-bait phrases
          3. Trim to WORD_COUNT_MAX words if over limit (clip at sentence boundary)

        Returns the repaired post. Never modifies the hook (first 2 lines) or the
        article link — only the tail and hashtag block.
        """
        lines = post_text.split("\n")

        # 1. Hashtag trimming — collect all tag lines, keep last MAX_HASHTAGS
        all_ht = re.findall(r'#\w+', post_text)
        if len(all_ht) > MAX_HASHTAGS:
            # Rebuild tag block with only the last MAX_HASHTAGS tags
            keep_tags = set(all_ht[-MAX_HASHTAGS:])
            new_lines = []
            for ln in lines:
                tags_in_line = re.findall(r'#\w+', ln)
                if tags_in_line and all(t in re.findall(r'#\w+', post_text) for t in tags_in_line):
                    # This is a hashtag-only line — rebuild it with kept tags only
                    kept = [t for t in tags_in_line if t in keep_tags]
                    if kept:
                        new_lines.append(" ".join(kept))
                    # else skip the line entirely
                else:
                    new_lines.append(ln)
            lines = new_lines

        # 2. Remove bait lines
        cleaned: list[str] = []
        for ln in lines:
            is_bait = any(
                re.search(pat, ln, re.IGNORECASE) for pat in _BAIT_PATTERNS
            )
            if not is_bait:
                cleaned.append(ln)
        lines = cleaned

        # 3. Trim to word count ceiling (preserve footer — last 3 lines)
        body_lines = lines[:-3] if len(lines) > 3 else lines
        footer_lines = lines[-3:] if len(lines) > 3 else []

        body_text = "\n".join(body_lines)
        words = body_text.split()
        if len(words) > WORD_COUNT_MAX:
            trimmed_words = words[:WORD_COUNT_MAX]
            trimmed = " ".join(trimmed_words)
            # Clip at last sentence boundary
            for i in range(len(trimmed) - 1, -1, -1):
                if trimmed[i] in ".!?":
                    trimmed = trimmed[:i + 1]
                    break
            body_lines = trimmed.split("\n")

        return "\n".join(body_lines + footer_lines)
