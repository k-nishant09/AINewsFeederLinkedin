"""
Comic Strip Generator for AIFeeders — 6-Scene Conversational Story Format.
v4 — unified News Card + Debate image (News -> Analysis -> Discussion):

SINGLE HERO IMAGE design — one image does both jobs:
  • Top 40% = NEWS CARD   — What happened? (headline, facts, source)
  • Bottom 60% = DEBATE   — What does it mean? (6-scene linked conversation)

The image is NOT two competing frames. It is one story with two layers:
  NEWS CARD tells facts -> the debate unpacks consequences -> caption drives discussion.

This eliminates the two-image problem: LinkedIn only guarantees the first image
is seen in feed preview. A single image ensures nobody misses the "news" half.

NEWS CARD layer (inside the same image, top section):
  • Dark header band: brand + "AI NEWS" badge
  • Bold headline (dominant, 2 lines max)
  • Subhead (tension / why now)
  • Yellow fact chips: 3-5 key numbers/claims from the article
  • Source attribution
  • "NEWS -> WHAT IT MEANS" transition tag bridges both layers

DEBATE layer (6 panels, 3 x 2 grid):
  Scene 1  MEDIA HOST — news brief + introduces the 4 named personas
  Scene 2  Persona A  — opens the debate (opportunity angle)
  Scene 3  Persona B  — replies to A (production-reality pushback)
  Scene 4  Persona C  — replies to B (second perspective)
  Scene 5  Persona D  — replies to C (second-order effect / governance)
  Scene 6  MEDIA HOST — synthesises + poses the audience question

OUTSIDE the image (LinkedIn post caption):
  Three jobs — none overlap with the image:
    Hook     — stop the scroll (2 punchy lines, new angle not in the image)
    Voices   — single line per persona with their core constraint
    CTA      — one audience question + source link + disclaimer + hashtags

Layout: 3 columns × 2 rows panels under the news card header.
SVG -> PNG via cairosvg -> rsvg-convert -> inkscape -> .svg fallback.
"""
from __future__ import annotations

import html
import logging
import textwrap
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Canvas constants ──────────────────────────────────────────────────────────
PANEL_W  = 480
PANEL_H  = 370        # slightly taller panels — more room for reply strip + bubble
COLS     = 3
ROWS     = 2
BORDER   = 3
PAD      = 12
# NEWS CARD header breakdown:
#   brand row (48px) + divider (8px) + headline (2 x 34px = 68px) +
#   subhead (26px) + fact-chip row (46px) + source row (24px) +
#   divider + bridge tag (26px) + bottom-padding (14px) = 260px
HEADER_H = 260
FOOTER_H = 54         # closing brand/CTA bar under the last row of panels

TOTAL_W = PANEL_W * COLS                        # 1440
TOTAL_H = PANEL_H * ROWS + HEADER_H + FOOTER_H  # 1054

# ── Palette ───────────────────────────────────────────────────────────────────
BG          = "#FFFFFF"
PANEL_BG    = "#F7F7F5"
BORDER_COL  = "#1A1A1A"
BUBBLE_BG   = "#FFFFFF"
TEXT_COL    = "#111111"
MUTED_TEXT  = "#6B6B6B"
HEADER_BG   = "#0F172A"
HEADER_TEXT = "#F8FAFC"
SUB_TEXT    = "#94A3B8"
NAME_FG     = "#0F172A"
SKIN        = "#FFDBAC"

# Per-persona accent colours
ACCENT: dict[str, str] = {
    "media":    "#2563EB",   # blue
    "business": "#16A34A",   # green
    "linkedin": "#7C3AED",   # purple
    "genz":     "#DC2626",   # red
    "policy":   "#D97706",   # amber
}

# Per-persona hair colour + emoji badge (used on the small face avatar)
_HAIR: dict[str, str] = {
    "media": "#3D2314", "business": "#2C1A0E", "linkedin": "#1A0A00",
    "genz": "#8B4513", "policy": "#4A3728",
}
_BADGE: dict[str, str] = {
    "media": "🎙️", "business": "💼", "linkedin": "🧑‍💻",
    "genz": "📊", "policy": "🏛️",
}


# ── Scene data container ──────────────────────────────────────────────────────

@dataclass
class ComicScene:
    """One panel in the 6-scene story strip."""
    persona:       str
    name:          str
    role:          str
    lines:         list[str] = field(default_factory=list)   # legacy, unused by renderer
    prev_persona:  str = ""
    prev_name:     str = ""
    prev_role:     str = ""
    prev_line:     str = ""    # the question/line this speaker is directly answering
    text:          str = ""    # raw (unwrapped) paragraph — bubble auto-sizes
    is_intro:      bool = False
    intro_lines:   list[str] = field(default_factory=list)
    host_question: str = ""    # Scene 1 only: the hand-off question to Persona A
    is_close:      bool = False


# ── Small FACE-ONLY avatar ───────────────────────────────────────────────────
# Deliberately tiny and simple — the words own the panel.

def _face(cx: int, cy: int, persona: str, r: int = 26, show_badge: bool = True) -> str:
    accent = ACCENT.get(persona, "#555555")
    hair   = _HAIR.get(persona, "#3D2314")
    badge  = _BADGE.get(persona, "🙂")
    br     = max(11, int(r * 0.42))

    svg = f"""<g transform="translate({cx},{cy})">
<circle cx="0" cy="0" r="{r}" fill="{SKIN}" stroke="{accent}" stroke-width="3.5"/>
<path d="M{-r+2},{-r*0.35:.1f} Q0,{-r*1.55:.1f} {r-2},{-r*0.35:.1f} Q{r-4},{-r*0.65:.1f} 0,{-r*0.7:.1f} Q{-r+4},{-r*0.65:.1f} {-r+2},{-r*0.35:.1f} Z" fill="{hair}"/>
<circle cx="{-r*0.32:.1f}" cy="{-r*0.05:.1f}" r="{max(2, int(r*0.09))}" fill="{BORDER_COL}"/>
<circle cx="{r*0.32:.1f}"  cy="{-r*0.05:.1f}" r="{max(2, int(r*0.09))}" fill="{BORDER_COL}"/>
<path d="M{-r*0.28:.1f},{r*0.35:.1f} Q0,{r*0.55:.1f} {r*0.28:.1f},{r*0.35:.1f}" stroke="{BORDER_COL}" stroke-width="2" fill="none"/>
"""
    if show_badge:
        svg += (f'<circle cx="{r*0.72:.1f}" cy="{r*0.72:.1f}" r="{br}" '
                f'fill="#FFFFFF" stroke="{accent}" stroke-width="2"/>\n'
                f'<text x="{r*0.72:.1f}" y="{r*0.72 + br*0.38:.1f}" '
                f'text-anchor="middle" font-family="Apple Color Emoji, Segoe UI Emoji, Noto Color Emoji, Android Emoji, EmojiSymbols, sans-serif" font-size="{br*1.15:.1f}">{badge}</text>\n')
    svg += "</g>"
    return svg


# ── Text helpers ──────────────────────────────────────────────────────────────

def _wrap(text: str, cols: int) -> list[str]:
    return textwrap.wrap(text.strip(), width=cols) or [""]


def _extract_question(text: str) -> str:
    """Extract the last question sentence from a speaker's line, or the last sentence."""
    import re
    text = text.strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return text
    for s in reversed(sentences):
        if s.endswith("?"):
            return s
    return sentences[-1]


def _grounded_question(candidate: str, spoken_text: str) -> str:
    """
    Only trust an explicitly-supplied pivot question if it is actually grounded
    in what the speaker's own bubble says. Otherwise, the quote strip on the
    NEXT panel attributes a question to someone that never appears in their visible dialogue.
    Falls back to extracting the question directly FROM the spoken text.
    """
    candidate = (candidate or "").strip()
    spoken_text = (spoken_text or "").strip()
    if candidate:
        import re
        def _keywords(s: str) -> set[str]:
            return {
                w for w in re.findall(r"[a-z]{3,}", s.lower())
                if w not in {
                    "this", "that", "with", "does", "what", "when", "where",
                    "actually", "really", "which", "about", "isn't", "aren't",
                    "from", "have", "been", "their", "there", "would", "could", "should", "the", "and"
                }
            }
        cand_kw, spoken_kw = _keywords(candidate), _keywords(spoken_text)
        if cand_kw and spoken_kw and len(cand_kw & spoken_kw) >= 1:
            return candidate
        logger.warning(
            "Discarding ungrounded pivot question (no keyword overlap with speaker's line): %r vs %r",
            candidate, spoken_text[:80],
        )
    return _extract_question(spoken_text)


def _fit_text_to_box(text: str, box_w: int, box_h: int,
                     max_font: int = 30, min_font: int = 14) -> tuple[int, list[str]]:
    """
    Pick the LARGEST font whose wrapped text still fits the box, so short
    lines fill the bubble and long lines shrink just enough to fit.
    """
    text    = text.strip()
    avail_w = box_w - 2 * PAD - 6
    avail_h = box_h - 2 * PAD
    CHAR_W  = 0.62
    for size in range(max_font, min_font - 1, -1):
        cols  = max(8, int(avail_w / (size * CHAR_W)))
        lines = textwrap.wrap(text, width=cols) or [""]
        lh    = size + 6
        if len(lines) * lh <= avail_h:
            return size, lines
    # nothing fit even at min_font — truncate
    size      = min_font
    cols      = max(8, int(avail_w / (size * CHAR_W)))
    lines     = textwrap.wrap(text, width=cols) or [""]
    max_lines = max(1, avail_h // (size + 6))
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [lines[max_lines - 1].rstrip()[:-1] + "…"] if max_lines > 0 else lines[:1]
    return size, lines


def _speech_bubble(
    x: int, y: int, w: int, h: int, text: str,
    tail_x_frac: float = 0.16, accent: str = BORDER_COL,
    max_font: int = 30, min_font: int = 14,
    tail_side: str = "top",   # "top" | "right"
) -> str:
    """Speech bubble that FILLS the given box — text auto-scales.

    tail_side="top"   Symmetric upward tail at x + w * tail_x_frac.
                      The dashed thread line from the reply strip above aligns with the tip.
    tail_side="right" Rightward tail at y + h * tail_x_frac (tail_x_frac reused as y-frac).
                      Used for col-2 panels where previous speaker is to the left.
    """
    rx_ = 12
    svg = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx_}" '
           f'fill="{BUBBLE_BG}" stroke="{accent}" stroke-width="2.5"/>\n')

    TW = 13   # half-width of tail base
    TH = 16   # height (length) of tail

    if tail_side == "right":
        # Tail points right from the right edge, centred vertically at tail_x_frac of h
        ty = y + int(h * max(0.15, min(0.85, tail_x_frac)))
        ex = x + w  # right edge
        svg += (f'<polygon points="{ex},{ty - TW} {ex + TH},{ty} {ex},{ty + TW}" '
                f'fill="{BUBBLE_BG}" stroke="{accent}" stroke-width="2.5"/>\n')
        svg += (f'<line x1="{ex - 1}" y1="{ty - TW + 1}" x2="{ex - 1}" y2="{ty + TW - 1}" '
                f'stroke="{BUBBLE_BG}" stroke-width="3.5"/>\n')
    else:
        # Symmetric upward tail: tip at (tx, y - TH), base from (tx-TW, y) to (tx+TW, y)
        tx = x + int(w * tail_x_frac)
        svg += (f'<polygon points="{tx - TW},{y} {tx},{y - TH} {tx + TW},{y}" '
                f'fill="{BUBBLE_BG}" stroke="{accent}" stroke-width="2.5"/>\n')
        svg += (f'<line x1="{tx - TW + 1}" y1="{y + 1}" x2="{tx + TW - 1}" y2="{y + 1}" '
                f'stroke="{BUBBLE_BG}" stroke-width="3.5"/>\n')

    font_size, lines = _fit_text_to_box(text, w, h, max_font=max_font, min_font=min_font)
    lh       = font_size + 6
    block_h  = len(lines) * lh
    y_offset = max(0, (h - 2 * PAD - block_h) // 2)

    for row, ln in enumerate(lines):
        ty_text = y + PAD + y_offset + font_size + row * lh
        weight  = "bold" if row == 0 else "normal"
        svg += (f'<text x="{x + PAD}" y="{ty_text}" '
                f'font-family="Georgia,Arial,sans-serif" font-size="{font_size}" '
                f'font-weight="{weight}" fill="{TEXT_COL}">'
                f'{html.escape(ln)}</text>\n')
    return svg


def _name_row(x: int, y: int, name: str, role: str, persona: str,
              face_r: int = 26) -> tuple[str, int]:
    """Face + bold NAME (20px) + ROLE (14px accent), compact header row. Returns (svg, row_height)."""
    accent = ACCENT.get(persona, "#555555")
    cx, cy = x + face_r, y + face_r
    svg    = _face(cx, cy, persona, r=face_r)

    tx = x + face_r * 2 + 12
    svg += (f'<text x="{tx}" y="{cy - 4}" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="20" font-weight="bold" fill="{NAME_FG}">'
            f'{html.escape(name.upper())}</text>\n')
    svg += (f'<text x="{tx}" y="{cy + 15}" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="14" font-weight="bold" fill="{accent}" letter-spacing="0.3">'
            f'{html.escape(role.upper())}</text>\n')
    return svg, face_r * 2 + 4


def _quote_reply_strip(x: int, y: int, w: int, prev_persona: str, prev_name: str,
                       prev_line: str, accent: str, is_close: bool = False,
                       tail_frac: float = 0.10) -> tuple[str, int]:
    """
    Chat-thread style reply strip — thick left accent bar + tinted bg + avatar + quoted line.
    Visually connects this panel to the previous speaker like a WhatsApp reply thread.

    tail_frac: the bubble tail's horizontal position (fraction of bubble width) so the
               dashed thread line descending from this strip aligns exactly with the tail.
    is_close=True (Scene 6): renders "🎙️ SYNTHESIZING THE DEBATE" synthesis bar.
    is_close=False (Scenes 2–5): chat-reply bar referencing the previous speaker's question.
    Returns (svg, strip_height).
    """
    h  = 54
    fr = 17

    # Thread connector x = strip_x + strip_w * tail_frac (clamped to safe range)
    thread_x = int(x + w * max(0.08, min(0.92, tail_frac)))

    # ── Scene 6 — synthesis strip ──────────────────────────────────────────────
    if is_close:
        svg = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" '
               f'fill="{accent}" opacity="0.10" stroke="{accent}" stroke-width="1.5" '
               f'stroke-opacity="0.25"/>\n')
        # Thick left accent bar
        svg += (f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="3" fill="{accent}"/>\n')
        svg += (f'<text x="{x + 14}" y="{y + 33}" font-family="Arial,Helvetica,sans-serif" '
                f'font-size="14" font-weight="bold" fill="{accent}">'
                f'🎙️  SYNTHESIZING THE DEBATE</text>\n')
        # Thread line descends from strip bottom to bubble tail — aligns with tail_frac
        svg += (f'<line x1="{thread_x}" y1="{y + h}" x2="{thread_x}" y2="{y + h + 10}" '
                f'stroke="{accent}" stroke-width="2" stroke-dasharray="3,3" opacity="0.50"/>\n')
        return svg, h

    # ── Scenes 2–5 — chat-reply bar ────────────────────────────────────────────
    quote = prev_line.strip().strip('"')
    max_chars = 58
    if len(quote) > max_chars:
        quote = quote[:max_chars].rsplit(" ", 1)[0] + "…"

    # Tinted background
    svg = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" '
           f'fill="{accent}" opacity="0.09" stroke="{accent}" stroke-width="1.5" '
           f'stroke-opacity="0.25"/>\n')
    # Thick left accent bar — the key "reply thread" visual cue
    svg += (f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="3" fill="{accent}"/>\n')

    # Tiny face of the person being quoted — no badge (keep it clean at small size)
    fcx, fcy = x + fr + 10, y + h // 2
    svg += _face(fcx, fcy, prev_persona, r=fr, show_badge=False)

    tx = x + fr * 2 + 18
    # "↩ SOPHIA:" — reply arrow + name, bold accent
    svg += (f'<text x="{tx}" y="{y + 18}" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="12" font-weight="bold" fill="{accent}" letter-spacing="0.4">'
            f'↩ {html.escape(prev_name.upper())}</text>\n')
    # Quoted line — italic, muted, slightly larger for readability
    svg += (f'<text x="{tx}" y="{y + 36}" font-family="Georgia,Arial,sans-serif" '
            f'font-size="13" font-style="italic" fill="{MUTED_TEXT}">'
            f'"{html.escape(quote)}"</text>\n')

    # Dashed thread line — descends to exactly where the bubble tail will point up from
    svg += (f'<line x1="{thread_x}" y1="{y + h}" x2="{thread_x}" y2="{y + h + 10}" '
            f'stroke="{accent}" stroke-width="2" stroke-dasharray="3,3" opacity="0.55"/>\n')
    return svg, h


def _host_question_pill(x: int, y: int, w: int, name: str, question: str,
                         accent: str) -> tuple[str, int]:
    """
    Scene 1 only: a distinct tinted pill at the bottom of the host's side showing
    the hand-off question to Persona A — separate from the news brief so the bubble
    stays clean. Returns (svg, pill_height).
    """
    h = 42
    q_short = question.strip().strip('"')
    if len(q_short) > 68:
        q_short = q_short[:68].rsplit(" ", 1)[0] + "…"

    svg = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
           f'fill="{accent}" opacity="0.12" stroke="{accent}" stroke-width="1.5" '
           f'stroke-opacity="0.40"/>\n')
    # Arrow icon pointing right (→) to signal hand-off
    svg += (f'<text x="{x + 10}" y="{y + 17}" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="13" font-weight="bold" fill="{accent}">→ {html.escape(name.upper())}:</text>\n')
    svg += (f'<text x="{x + 10}" y="{y + 33}" font-family="Georgia,Arial,sans-serif" '
            f'font-size="13" font-style="italic" fill="{MUTED_TEXT}">'
            f'{html.escape(q_short)}</text>\n')
    return svg, h


# ── Panel renderers ────────────────────────────────────────────────────────────

# Circled number characters for panel sequence badges
_SEQUENCE_BADGE = ["①", "②", "③", "④", "⑤", "⑥"]


def _panel_frame(px: int, py: int, accent: str, scene_idx: int = 0) -> str:
    """Panel background + top accent stripe + sequence badge."""
    svg = (f'<rect x="{px + BORDER}" y="{py + BORDER}" '
           f'width="{PANEL_W - BORDER * 2}" height="{PANEL_H - BORDER * 2}" '
           f'fill="{PANEL_BG}" stroke="{BORDER_COL}" stroke-width="{BORDER}" rx="8"/>\n')
    # Top accent stripe
    svg += (f'<rect x="{px + BORDER}" y="{py + BORDER}" '
            f'width="{PANEL_W - BORDER * 2}" height="7" rx="3" fill="{accent}"/>\n')
    # Sequence badge — top-right corner pill
    badge = _SEQUENCE_BADGE[min(scene_idx, 5)]
    bx = px + PANEL_W - BORDER - 34
    by = py + BORDER + 2
    svg += (f'<rect x="{bx}" y="{by}" width="28" height="22" rx="6" '
            f'fill="{accent}" opacity="0.92"/>\n')
    svg += (f'<text x="{bx + 14}" y="{by + 15}" text-anchor="middle" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="13" '
            f'font-weight="bold" fill="#FFFFFF">{badge}</text>\n')
    return svg


def _panel_connector_arrow(px: int, py: int, direction: str = "right") -> str:
    """
    Draw a small arrow on the panel border showing the reply direction.
    direction='right': arrow points right (current speaker replied, next panel continues)
    direction='down':  arrow points down (row break — reply continues below)
    """
    if direction == "right":
        # Arrow on the right edge, vertically centred
        ax = px + PANEL_W - BORDER
        ay = py + PANEL_H // 2
        return (f'<polygon points="{ax},{ay} {ax-14},{ay-9} {ax-14},{ay+9}" '
                f'fill="{BORDER_COL}" opacity="0.28"/>\n')
    if direction == "down":
        # Arrow on the bottom edge, horizontally centred
        ax = px + PANEL_W // 2
        ay = py + PANEL_H - BORDER
        return (f'<polygon points="{ax},{ay} {ax-9},{ay-14} {ax+9},{ay-14}" '
                f'fill="{BORDER_COL}" opacity="0.28"/>\n')
    return ""


def _ghost_listener(px: int, py: int, prev_persona: str, col: int) -> str:
    """
    Minimal ghost silhouette of the PREVIOUS speaker — bottom-far corner of panel.
    Purely a head+shoulder shape, no badge, no eyes — just enough to suggest presence.
    col=2 (right panel) → ghost on left side; otherwise ghost on right.
    """
    r = 18
    # Far corner: opposite to speaker's name row (top-left)
    cx = px + BORDER + r + 14 if col == 2 else px + PANEL_W - BORDER - r - 14
    cy = py + PANEL_H - BORDER - r - 14
    accent = ACCENT.get(prev_persona, "#888888")
    hair   = _HAIR.get(prev_persona, "#3D2314")

    svg  = '<g opacity="0.15">\n'
    # Head circle
    svg += (f'<circle cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="{SKIN}" stroke="{accent}" stroke-width="1.5"/>\n')
    # Hair: simple filled semicircle cap — M left Q top-ctrl right Z
    svg += (f'<path d="M {cx - r + 2} {cy - int(r * 0.2)} '
            f'Q {cx} {cy - r * 2} {cx + r - 2} {cy - int(r * 0.2)} Z" '
            f'fill="{hair}"/>\n')
    # Shoulder arc below — suggests a body is there
    svg += (f'<path d="M {cx - r - 4} {cy + r + 2} '
            f'Q {cx} {cy + r - 4} {cx + r + 4} {cy + r + 2}" '
            f'stroke="{accent}" stroke-width="1.5" fill="none"/>\n')
    svg += '</g>\n'
    return svg


def _render_scene1(px: int, py: int, scene: ComicScene) -> str:
    """Scene 1 — host brief (left) + hand-off question pill + 4-row cast roster (right)."""
    c   = ACCENT.get(scene.persona, "#2563EB")
    svg = _panel_frame(px, py, c, scene_idx=0)

    split = int(PANEL_W * 0.52)   # slightly more room for host brief
    div_x = px + split
    svg += (f'<line x1="{div_x}" y1="{py + 16}" x2="{div_x}" y2="{py + PANEL_H - 14}" '
            f'stroke="{BORDER_COL}" stroke-width="1" opacity="0.12"/>\n')

    # LEFT: host name row
    lx = px + BORDER + 10
    ly = py + BORDER + 12
    row_svg, row_h = _name_row(lx, ly, scene.name, scene.role, scene.persona, face_r=24)
    svg += row_svg

    cursor_y = ly + row_h + 8

    # Hand-off question pill (if present) — rendered BELOW the bubble, not inside it
    pill_h = 0
    pill_svg = ""
    if scene.host_question:
        pill_w = split - BORDER - 20
        # Reserve space at bottom; render after bubble
        pill_h = 50   # approximate; actual returned below
        pill_svg, pill_h = _host_question_pill(lx, 0, pill_w, scene.intro_lines[0].split(" — ")[0].strip().lstrip("💼🧑‍💻⚖️🏛️🎙️ ").strip(), scene.host_question, c)

    # Speech bubble fills the space between name row and pill
    bubble_w = split - BORDER - 20
    bottom_margin = pill_h + 10 if scene.host_question else 14
    bubble_h = PANEL_H - (cursor_y - py) - bottom_margin
    svg += _speech_bubble(lx, cursor_y, bubble_w, bubble_h, scene.text,
                          tail_x_frac=0.08, accent=c, max_font=19, min_font=12)

    # Render pill below bubble
    if scene.host_question:
        pill_y = cursor_y + bubble_h + 4
        pill_svg, _ = _host_question_pill(lx, pill_y, bubble_w, scene.intro_lines[0].split(" — ")[0].strip().lstrip("💼🧑‍💻⚖️🏛️🎙️ ").strip(), scene.host_question, c)
        svg += pill_svg

    # RIGHT: TODAY'S VOICES — 4 rows, larger text
    rx0 = div_x + 12
    svg += (f'<text x="{rx0}" y="{py + BORDER + 22}" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="14" font-weight="bold" fill="{MUTED_TEXT}" letter-spacing="1.6">'
            f"TODAY'S VOICES</text>\n")

    row_h2        = (PANEL_H - BORDER * 2 - 34) // 4
    persona_order = ["business", "linkedin", "genz", "policy"]
    for i, card_line in enumerate(scene.intro_lines[:4]):
        ry    = py + BORDER + 34 + i * row_h2
        pkey  = persona_order[i]
        row_c = ACCENT.get(pkey, c)
        parts = card_line.split(" — ", 1)
        nm    = parts[0].strip()
        for ch in "💼🧑‍💻⚖️🏛️🎙️ ":
            nm = nm.lstrip(ch)
        nm = nm.strip()
        rl = parts[1].strip() if len(parts) > 1 else ""

        face_r       = 18
        cx_, cy_     = rx0 + face_r, ry + row_h2 // 2
        svg += _face(cx_, cy_, pkey, r=face_r)
        tx = rx0 + face_r * 2 + 10
        svg += (f'<text x="{tx}" y="{cy_ - 4}" font-family="Arial,Helvetica,sans-serif" '
                f'font-size="16" font-weight="bold" fill="{TEXT_COL}">{html.escape(nm)}</text>\n')
        svg += (f'<text x="{tx}" y="{cy_ + 14}" font-family="Arial,Helvetica,sans-serif" '
                f'font-size="13" fill="{row_c}" font-weight="bold">{html.escape(rl)}</text>\n')
    return svg


def _render_conversation_scene(px: int, py: int, scene: ComicScene,
                                scene_idx: int = 1) -> str:
    """Scenes 2-6 — name row + chat-reply strip + speech bubble with directional tail."""
    spk_c = ACCENT.get(scene.persona, "#555555")
    col   = scene_idx % COLS   # 0, 1, or 2 — drives tail direction + ghost position
    svg   = _panel_frame(px, py, spk_c, scene_idx=scene_idx)

    # Ghost listener — faded previous speaker in far corner
    if scene.prev_persona and not scene.is_close:
        svg += _ghost_listener(px, py, scene.prev_persona, col)

    hx = px + BORDER + 10
    hy = py + BORDER + 12
    row_svg, row_h = _name_row(hx, hy, scene.name, scene.role, scene.persona, face_r=26)
    svg += row_svg

    cursor_y = hy + row_h + 6

    # Tail geometry — compute BEFORE strip so the strip's thread line aligns with the tail tip.
    #   col 0 / col 1  → top tail, left side (frac=0.10) — speaker faces right toward next panel
    #   col 2          → right-side tail (frac=0.35 of bubble height) — faces left to prev panel
    #   Scene 6        → top tail, centred (frac=0.50) — synthesis, no directional reply
    if scene.is_close:
        tail_side  = "top"
        tail_frac  = 0.50
    elif col == 2:
        tail_side  = "right"
        tail_frac  = 0.35   # 35% down the bubble height = roughly name-row level
    else:
        tail_side  = "top"
        tail_frac  = 0.10

    # Chat-reply strip:
    #  • Scenes 2-5: shown when prev_name+prev_line are set
    #  • Scene 6 (is_close): synthesis strip always shown
    # For col-2 (right-tail) the strip thread_x is ignored — tail is on right side, not top
    strip_h = 0
    strip_x = px + BORDER + 8
    strip_w = PANEL_W - BORDER * 2 - 16
    strip_tail_frac = tail_frac if tail_side == "top" else 0.10   # strip always drops down
    if scene.is_close:
        strip_svg, strip_h = _quote_reply_strip(
            strip_x, cursor_y, strip_w,
            "", "", "",
            spk_c, is_close=True, tail_frac=strip_tail_frac,
        )
        svg += strip_svg
        cursor_y += strip_h + 10
    elif scene.prev_name and scene.prev_line:
        strip_svg, strip_h = _quote_reply_strip(
            strip_x, cursor_y, strip_w,
            scene.prev_persona, scene.prev_name, scene.prev_line,
            spk_c, is_close=False, tail_frac=strip_tail_frac,
        )
        svg += strip_svg
        cursor_y += strip_h + 10

    bubble_x = px + BORDER + 8
    bubble_w = PANEL_W - BORDER * 2 - 16
    bubble_h = PANEL_H - (cursor_y - py) - 12

    # Scene 6 synthesis: slightly tinted bubble bg to signal "wrap-up" visually
    if scene.is_close:
        svg += (f'<rect x="{bubble_x}" y="{cursor_y}" width="{bubble_w}" height="{bubble_h}" '
                f'rx="12" fill="{spk_c}" opacity="0.06"/>\n')

    svg += _speech_bubble(bubble_x, cursor_y, bubble_w, bubble_h, scene.text,
                          tail_x_frac=tail_frac, accent=spk_c, max_font=24, min_font=14,
                          tail_side=tail_side)
    return svg


def _render_scene(px: int, py: int, scene: ComicScene, scene_idx: int = 0) -> str:
    if scene.is_intro:
        return _render_scene1(px, py, scene)
    return _render_conversation_scene(px, py, scene, scene_idx=scene_idx)


# ── Full comic strip render ───────────────────────────────────────────────────

def _news_card_header(
    headline: str,
    subhead: str,
    facts: list[str] | None,
    source_name: str,
    brand: str,
) -> str:
    """
    Render the full NEWS CARD section that occupies the top HEADER_H pixels.

    Visual hierarchy (top to bottom):
      1. Brand row: logo + name + tagline + "AI NEWS" badge        ~48 px
      2. Thin divider                                               ~8 px
      3. Headline (1-2 lines, dominant)                           ~68 px
      4. Subhead / tension line                                    ~26 px
      5. Fact chips row (key numbers)                              ~46 px
      6. Source attribution                                        ~24 px
      7. Horizontal divider                                        ~8 px
      8. "NEWS  ──  WHAT IT MEANS ↓" bridge tag                  ~26 px
      Total: ~254 px (fits inside HEADER_H = 260)
    """
    svg = ""

    # ── Background gradient ──────────────────────────────────────────────────
    svg += f'<rect x="0" y="0" width="{TOTAL_W}" height="{HEADER_H}" fill="{HEADER_BG}"/>\n'
    # Subtle blue radial glow — top-left origin, creates depth
    svg += (f'<radialGradient id="hdrGlow" cx="8%" cy="0%" r="70%">'
            f'<stop offset="0%" stop-color="#2563EB" stop-opacity="0.22"/>'
            f'<stop offset="100%" stop-color="#2563EB" stop-opacity="0"/>'
            f'</radialGradient>\n')
    svg += f'<rect x="0" y="0" width="{TOTAL_W}" height="{HEADER_H}" fill="url(#hdrGlow)"/>\n'
    # Left accent stripe — the visual anchor that ties brand row to panels
    svg += f'<rect x="0" y="0" width="8" height="{HEADER_H}" fill="#2563EB"/>\n'

    # ── "AI NEWS" live badge — top-right corner ──────────────────────────────
    bw, bh = 108, 30
    bx, by = TOTAL_W - bw - 20, 14
    svg += (f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="{bh / 2}" '
            f'fill="#DC2626"/>\n')
    # Pulsing live dot
    svg += (f'<circle cx="{bx + 16}" cy="{by + bh // 2}" r="4" fill="#FFFFFF">'
            f'<animate attributeName="opacity" values="1;0.3;1" dur="1.6s" repeatCount="indefinite"/>'
            f'</circle>\n')
    svg += (f'<text x="{bx + 28}" y="{by + bh // 2 + 5}" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="14" font-weight="900" '
            f'letter-spacing="1" fill="#FFFFFF">AI NEWS</text>\n')

    # ── Brand row ─────────────────────────────────────────────────────────────
    svg += f'<rect x="18" y="10" width="40" height="40" rx="10" fill="#2563EB"/>\n'
    svg += (f'<text x="38" y="35" text-anchor="middle" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="22" fill="#FFFFFF">🧠</text>\n')
    svg += (f'<text x="68" y="38" font-family="Arial Black,Arial,Helvetica,sans-serif" '
            f'font-size="26" font-weight="900" letter-spacing="2.5" fill="#FFFFFF">'
            f'{html.escape(brand.upper())}</text>\n')
    brand_end_x = 68 + len(brand) * 20 + 16
    svg += (f'<text x="{brand_end_x}" y="38" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="16" font-weight="bold" letter-spacing="0.8" fill="#60A5FA">'
            f'·  ONE NEWS.  MULTIPLE REAL-WORLD VOICES.</text>\n')

    # Divider below brand row
    div1_y = 56
    svg += (f'<line x1="18" y1="{div1_y}" x2="{TOTAL_W - 18}" y2="{div1_y}" '
            f'stroke="#2563EB" stroke-width="1.5" opacity="0.35"/>\n')

    # ── Headline — dominant, scroll-stopping ──────────────────────────────────
    hl_start_y = div1_y + 10
    hl_lines = _wrap(headline, cols=52)[:2]
    hl_fs    = 36 if len(hl_lines) == 1 else 29
    hl_lh    = hl_fs + 9
    for i, ln in enumerate(hl_lines):
        svg += (f'<text x="18" y="{hl_start_y + hl_fs + i * hl_lh}" '
                f'font-family="Arial Black,Arial,Helvetica,sans-serif" '
                f'font-size="{hl_fs}" font-weight="900" fill="{HEADER_TEXT}">'
                f'{html.escape(ln)}</text>\n')
    hl_end_y = hl_start_y + len(hl_lines) * hl_lh + 4

    # ── Subhead — tension / why now ───────────────────────────────────────────
    cursor_y = hl_end_y
    if subhead:
        svg += (f'<text x="18" y="{cursor_y + 22}" '
                f'font-family="Arial,Helvetica,sans-serif" '
                f'font-size="18" fill="#FCD34D">'
                f'{html.escape(subhead[:118])}</text>\n')
        cursor_y += 30

    # ── Fact chips — the "what happened in numbers" news-card layer ───────────
    # Each fact is a yellow pill with bold dark text — scannable at a glance.
    cursor_y += 8
    if facts:
        cx_ = 18
        max_x = TOTAL_W - 20
        chip_h = 32
        for fact in facts[:5]:
            fact = fact.strip()
            if not fact:
                continue
            chip_w = min(len(fact) * 9 + 28, 320)
            if cx_ + chip_w > max_x:
                # wrap would go off-canvas — stop here rather than wrapping
                break
            svg += (f'<rect x="{cx_}" y="{cursor_y}" width="{chip_w}" height="{chip_h}" '
                    f'rx="{chip_h / 2}" fill="#FCD34D"/>\n')
            svg += (f'<text x="{cx_ + chip_w / 2:.0f}" y="{cursor_y + 22}" '
                    f'text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
                    f'font-size="14" font-weight="900" fill="#0F172A">'
                    f'{html.escape(fact)}</text>\n')
            cx_ += chip_w + 10
        cursor_y += chip_h + 6

    # ── Source attribution ────────────────────────────────────────────────────
    if source_name:
        cursor_y += 4
        svg += (f'<text x="18" y="{cursor_y + 16}" '
                f'font-family="Arial,Helvetica,sans-serif" font-size="14" '
                f'font-weight="bold" fill="#94A3B8" letter-spacing="0.5">'
                f'SOURCE: {html.escape(source_name.upper()[:30])}</text>\n')
        cursor_y += 22

    # ── Horizontal divider — visually separates NEWS CARD from DEBATE ─────────
    cursor_y += 6
    div2_y = cursor_y
    svg += (f'<line x1="0" y1="{div2_y}" x2="{TOTAL_W}" y2="{div2_y}" '
            f'stroke="#2563EB" stroke-width="2" opacity="0.5"/>\n')

    # ── Bridge tag — teaches the reader the format in one glance ─────────────
    tag_y = div2_y + 6
    svg += (f'<text x="18" y="{tag_y + 14}" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="13" font-weight="bold" letter-spacing="1.8" '
            f'fill="#2563EB" opacity="0.80">NEWS CARD</text>\n')
    mid_x = TOTAL_W // 2
    svg += (f'<text x="{mid_x}" y="{tag_y + 14}" text-anchor="middle" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="13" '
            f'font-weight="bold" letter-spacing="1.5" fill="#60A5FA" opacity="0.80">'
            f'━━━  WHAT IT MEANS  ↓  ━━━</text>\n')
    svg += (f'<text x="{TOTAL_W - 18}" y="{tag_y + 14}" text-anchor="end" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="13" '
            f'font-weight="bold" letter-spacing="1.8" '
            f'fill="#2563EB" opacity="0.80">DEBATE</text>\n')

    return svg


def _render_comic(
    headline:    str,
    subhead:     str,
    scenes:      list[ComicScene],
    brand:       str = "AIFeeders",
    facts:       list[str] | None = None,
    source_name: str = "",
) -> str:
    """Render the full AIFeeders comic SVG: News Card header + 6 debate panels + footer."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'width="{TOTAL_W}" height="{TOTAL_H}" viewBox="0 0 {TOTAL_W} {TOTAL_H}">\n')

    # Reusable filter defs — soft shadow on panels and badges
    svg += (
        '<defs>\n'
        '<filter id="softShadow" x="-20%" y="-20%" width="140%" height="140%">'
        '<feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#0F172A" flood-opacity="0.18"/>'
        '</filter>\n'
        '<filter id="badgeShadow" x="-50%" y="-50%" width="200%" height="200%">'
        '<feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#000000" flood-opacity="0.35"/>'
        '</filter>\n'
        '</defs>\n'
    )

    svg += f'<rect width="{TOTAL_W}" height="{TOTAL_H}" fill="{BG}"/>\n'

    # ── News Card header (top 40%) ─────────────────────────────────────────────
    svg += _news_card_header(headline, subhead, facts, source_name, brand)

    # ── Debate panels (middle 54%) ─────────────────────────────────────────────
    for i, scene in enumerate(scenes[:6]):
        col, row = i % COLS, i // COLS
        px, py   = col * PANEL_W, HEADER_H + row * PANEL_H
        svg += _render_scene(px, py, scene, scene_idx=i)

    # Connector arrows drawn AFTER panels — sit in the inter-panel gutters
    for i in range(5):
        col, row = i % COLS, i // COLS
        px, py   = col * PANEL_W, HEADER_H + row * PANEL_H
        next_scene = scenes[i + 1] if i + 1 < len(scenes) else None
        arr_c = ACCENT.get(next_scene.persona, HEADER_BG) if next_scene else HEADER_BG

        if col < COLS - 1:
            ax = px + PANEL_W
            ay = py + PANEL_H // 2
            svg += (f'<circle cx="{ax}" cy="{ay}" r="13" fill="#FFFFFF" opacity="0.92"/>\n')
            svg += (f'<polygon points="{ax + 10},{ay} {ax - 4},{ay - 8} {ax - 4},{ay + 8}" '
                    f'fill="{arr_c}" opacity="0.80"/>\n')
        elif col == COLS - 1:
            ax = TOTAL_W // 2
            ay = py + PANEL_H
            svg += (f'<circle cx="{ax}" cy="{ay}" r="13" fill="#FFFFFF" opacity="0.92"/>\n')
            svg += (f'<polygon points="{ax},{ay + 10} {ax - 8},{ay - 4} {ax + 8},{ay - 4}" '
                    f'fill="{arr_c}" opacity="0.80"/>\n')

    # ── Closing footer bar (bottom 5%) ─────────────────────────────────────────
    fy = HEADER_H + PANEL_H * ROWS
    svg += f'<rect x="0" y="{fy}" width="{TOTAL_W}" height="{FOOTER_H}" fill="{HEADER_BG}"/>\n'
    svg += f'<rect x="0" y="{fy}" width="8" height="{FOOTER_H}" fill="#2563EB"/>\n'
    svg += (f'<text x="20" y="{fy + FOOTER_H // 2 + 5}" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="15" '
            f'font-weight="bold" fill="#F8FAFC">'
            f'🤖 AIFeeders · Daily AI Intelligence</text>\n')
    svg += (f'<text x="{TOTAL_W - 20}" y="{fy + FOOTER_H // 2 + 5}" text-anchor="end" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="15" '
            f'font-weight="bold" fill="#FCD34D">Where do you stand? 👇</text>\n')

    svg += "</svg>\n"
    return svg


# ── SVG → PNG conversion ──────────────────────────────────────────────────────

def _svg_to_png(svg_str: str, out_path: Path) -> Path:
    import subprocess
    svg_path = out_path.with_suffix(".svg")
    svg_path.write_text(svg_str, encoding="utf-8")

    # ── Attempt 1: cairosvg (pip-installed, pure-Python SVG parser) ───────────
    # Pass bytestring directly — avoids any file-URI resolution issues.
    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(
            bytestring=svg_str.encode("utf-8"),
            write_to=str(out_path),
            output_width=TOTAL_W,
            output_height=TOTAL_H,
        )
        svg_path.unlink(missing_ok=True)
        logger.info("comic: cairosvg rendered %s (%d bytes)", out_path.name, out_path.stat().st_size)
        return out_path
    except ImportError:
        logger.debug("comic: cairosvg not installed — trying CLI converters")
    except Exception as e:          # OSError (missing .so), ValueError, etc.
        logger.warning("comic: cairosvg failed (%s) — trying CLI converters", e)

    # ── Attempt 2: rsvg-convert / inkscape ────────────────────────────────────
    for cmd in (["rsvg-convert", "-o", str(out_path), str(svg_path)],
                ["inkscape", "--export-type=png", f"--export-filename={out_path}", str(svg_path)]):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=20)
            if r.returncode == 0:
                svg_path.unlink(missing_ok=True)
                logger.info("comic: %s rendered %s", cmd[0], out_path.name)
                return out_path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    logger.warning("comic: no SVG→PNG converter available — returning .svg path")
    return svg_path


# ── Script data model ─────────────────────────────────────────────────────────

@dataclass
class PersonaCast:
    name:    str
    role:    str
    persona: str     # business | linkedin | genz | policy
    emoji:   str


@dataclass
class ComicScript:
    headline:          str
    host_name:         str
    host_role:         str
    news_brief:        str
    central_tension:   str
    cast:              list[PersonaCast]
    voice1_line:       str
    voice2_line:       str
    voice3_line:       str
    voice4_line:       str
    host_synthesis:    str
    audience_question: str
    host_question:     str = ""   # specific question Media poses to Persona A — shown as pill in Scene 1
    # Explicit causal question bridges (set from PersonaOutput.next_question)
    # question1 = host → A, question2 = A → B, question3 = B → C, question4 = C → D
    question1:         str = ""
    question2:         str = ""
    question3:         str = ""
    question4:         str = ""
    hashtags:          list[str] = field(default_factory=list)
    source_url:        str = ""
    source_name:       str = ""
    # Key facts extracted from the article — rendered as yellow chips in the News Card.
    # Format: short strings like "$42B NET LOSS", "$4.6B REVENUE", "$518B CLOUD CAPEX"
    facts:             list[str] = field(default_factory=list)
    choices:           list[str] = field(default_factory=list)  # backwards-compat


# ── Script → Scene builder ────────────────────────────────────────────────────

def _split_lines(text: str, cols: int = 26, cap: int = 8) -> list[str]:
    parts   = text.strip().split("\n")
    wrapped = []
    for part in parts:
        part = part.strip()
        wrapped.append("") if not part else wrapped.extend(_wrap(part, cols=cols))
    return wrapped[:cap]


def build_scenes(script: ComicScript) -> list[ComicScene]:
    cast = script.cast
    if len(cast) < 4:
        raise ValueError(f"ComicScript.cast must have exactly 4 personas, got {len(cast)}")
    A, B, C, D = cast[0], cast[1], cast[2], cast[3]

    # ── Scene 1: brief stays clean — hand-off question is a separate pill ──────
    brief_text = script.news_brief.strip()
    # The host_question (if set) is shown as a distinct pill, NOT appended to brief
    host_q = getattr(script, "host_question", "") or ""
    if not host_q and script.central_tension:
        host_q = script.central_tension.strip()

    intro_lines = [f"{p.emoji} {p.name} — {p.role}" for p in cast]
    host_cast   = PersonaCast(script.host_name, script.host_role, "media", "🎙️")

    scene1 = ComicScene(
        persona="media", name=script.host_name, role=script.host_role,
        text=brief_text, is_intro=True, intro_lines=intro_lines,
        host_question=host_q,
    )

    def _conv(speaker: PersonaCast, listener: PersonaCast,
              line: str, prev_line: str = "", close: bool = False) -> ComicScene:
        return ComicScene(
            persona=speaker.persona, name=speaker.name, role=speaker.role,
            text=line.strip(),
            prev_persona=listener.persona, prev_name=listener.name,
            prev_role=listener.role, prev_line=prev_line.strip(),
            is_close=close,
        )

    # Each persona answers the previous speaker's question — validated with grounding
    # check against the speaker's actual text to prevent disconnects between panels.
    q1 = _grounded_question(getattr(script, "question1", "") or host_q, brief_text)
    q2 = _grounded_question(getattr(script, "question2", ""), script.voice1_line)
    q3 = _grounded_question(getattr(script, "question3", ""), script.voice2_line)
    q4 = _grounded_question(getattr(script, "question4", ""), script.voice3_line)

    scene2 = _conv(A, host_cast, script.voice1_line, prev_line=q1)
    scene3 = _conv(B, A,         script.voice2_line, prev_line=q2)
    scene4 = _conv(C, B,         script.voice3_line, prev_line=q3)
    scene5 = _conv(D, C,         script.voice4_line, prev_line=q4)
    # Scene 6: synthesis — no previous-speaker reference, is_close suppresses avatar
    scene6 = ComicScene(
        persona="media", name=script.host_name, role=script.host_role,
        text=script.host_synthesis.strip(),
        prev_persona="", prev_name="", prev_role="", prev_line="",
        is_close=True,
    )

    return [scene1, scene2, scene3, scene4, scene5, scene6]


# ── Outside-image LinkedIn post text ─────────────────────────────────────────

def _clean_summary_point(text: str, max_len: int = 200) -> str:
    """
    Extract one complete sentence from a persona's voice line for the caption.

    Rules:
    - Always return a complete sentence (ends with . ! or ?).
    - If the first sentence is ≤ max_len chars, return it in full — no truncation.
    - If the first sentence is longer than max_len, try the second sentence instead.
    - Only truncate with … as a last resort when even the shortest available
      sentence exceeds max_len.
    - Never return a sentence that ends mid-word with ….
    """
    import re as _re_sp
    cleaned = (text or "").strip().strip('"').strip()
    if not cleaned:
        return ""

    # Split into complete sentences at . ! ?
    # Keep the delimiter attached to the sentence (lookahead split)
    sentences = [s.strip() for s in _re_sp.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    if not sentences:
        return cleaned[:max_len]

    # Return the first sentence that is ≤ max_len — in full, untruncated
    for s in sentences[:3]:
        if len(s) <= max_len:
            return s

    # All candidate sentences are longer than max_len — truncate the shortest one
    shortest = min(sentences[:3], key=len)
    cut = shortest[:max_len].rsplit(" ", 1)[0].rstrip(" .,;—–")
    return cut + "…"


def build_outside_post(script: ComicScript) -> str:
    """
    LinkedIn post caption — lean 3-part format.

    The comic image carries the full debate.
    The caption's only jobs:

      PART 1 — HOOK  (1 bold tagline + 1 pitch line — stop the scroll)
      PART 2 — BRIDGE (point to the image; 4 emoji-name teasers, ≤8 words each)
      PART 3 — CTA   (host question + source + disclaimer + hashtags)

    Rules:
    - Never repeat the full persona voice from the image.
    - Each voice teaser is the persona's NAME + ROLE + their ONE key word/phrase.
    - No paragraph text anywhere in the caption.
    """
    import re as _re_p

    lines: list[str] = []

    # ── PART 1: HOOK — bold AI NEWS tagline + 1-line pitch ───────────────────
    # Tagline: bold headline (unicode bold so it renders in LinkedIn feed)
    headline = (script.headline or "").strip()
    def _bold(text: str) -> str:
        """Render text in Unicode bold sans-serif (renders bold in LinkedIn)."""
        normal = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        )
        bold_map = {
            'A':'𝗔','B':'𝗕','C':'𝗖','D':'𝗗','E':'𝗘','F':'𝗙','G':'𝗚','H':'𝗛',
            'I':'𝗜','J':'𝗝','K':'𝗞','L':'𝗟','M':'𝗠','N':'𝗡','O':'𝗢','P':'𝗣',
            'Q':'𝗤','R':'𝗥','S':'𝗦','T':'𝗧','U':'𝗨','V':'𝗩','W':'𝗪','X':'𝗫',
            'Y':'𝗬','Z':'𝗭',
            'a':'𝗮','b':'𝗯','c':'𝗰','d':'𝗱','e':'𝗲','f':'𝗳','g':'𝗴','h':'𝗵',
            'i':'𝗶','j':'𝗷','k':'𝗸','l':'𝗹','m':'𝗺','n':'𝗻','o':'𝗼','p':'𝗽',
            'q':'𝗾','r':'𝗿','s':'𝘀','t':'𝘁','u':'𝘂','v':'𝘃','w':'𝘄','x':'𝘅',
            'y':'𝘆','z':'𝘇',
            '0':'𝟬','1':'𝟭','2':'𝟮','3':'𝟯','4':'𝟰','5':'𝟱','6':'𝟲','7':'𝟳',
            '8':'𝟴','9':'𝟵',
        }
        return "".join(bold_map.get(c, c) for c in text)

    # Line 1: "🧠 AI NEWS  |  <bold headline>"
    lines.append(f"🧠 {_bold('AI NEWS')}  |  {_bold(headline)}")

    # Line 2: one-line pitch — the central tension or first fact
    facts   = getattr(script, "facts", []) or []
    tension = (script.central_tension or "").strip()
    brief   = (script.news_brief or "").strip()
    brief_s = [s.strip() for s in _re_p.split(r"(?<=[.!?])\s+", brief) if s.strip()]

    pitch = tension or (facts[0] if facts else "") or (brief_s[0] if brief_s else "")
    # Keep pitch to one sentence, ≤120 chars
    pitch = _re_p.split(r"(?<=[.!?])\s+", pitch)[0] if pitch else ""
    if len(pitch) > 120:
        pitch = pitch[:117].rstrip() + "…"
    if pitch:
        lines.append(pitch)
    lines.append("")

    # ── PART 2: BRIDGE — image pointer + 4 name teasers ─────────────────────
    lines.append("👇 See image — four voices, one story.")
    lines.append("")

    if script.cast and len(script.cast) >= 4:
        voice_lines = [
            script.voice1_line,
            script.voice2_line,
            script.voice3_line,
            script.voice4_line,
        ]
        for p, vline in zip(script.cast[:4], voice_lines):
            # Extract the single most concrete noun/phrase from their line (≤7 words)
            clean = _clean_summary_point(vline or "")
            # Trim to first clause (up to comma or semicolon), max 7 words
            clause = _re_p.split(r"[,;—]", clean)[0].strip() if clean else ""
            words  = clause.split()
            teaser = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
            if teaser:
                lines.append(f"{p.emoji} {_bold(p.name)} → {teaser}")
        lines.append("")

    lines.append("Their answers don't completely agree.")
    lines.append("That's exactly the point.")
    lines.append("")

    # ── PART 3: CTA — host question + source + disclaimer + hashtags ─────────
    lines.append(f"🎙️ {_bold(script.host_name)} — {_bold('THE AIFEEDERS QUESTION:')}")
    lines.append("")
    audience_q = (script.audience_question or "").strip()
    if audience_q:
        lines.append(audience_q)
    lines.append("")

    if script.source_name and script.source_url:
        lines.append(f"📰 Source: {script.source_name}")
        lines.append(f"🔗 {script.source_url}")
    elif script.source_url:
        lines.append(f"🔗 {script.source_url}")
    elif script.source_name:
        lines.append(f"📰 Source: {script.source_name}")

    lines += [
        "",
        "⚠️ Perspectives are AI-simulated for discussion — not professional advice.",
        "🤖 AIFeeders · Daily AI Intelligence · Powered by Jev",
    ]

    if script.hashtags:
        lines += ["", " ".join(h if h.startswith("#") else f"#{h}" for h in script.hashtags[:4])]
    else:
        lines += ["", "#EnterpriseAI #AIInfrastructure #AIGovernance"]

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_comic_from_script(
    script:   ComicScript,
    run_id:   Optional[str] = None,
    out_dir:  str = "/tmp",
    subhead:  str = "",
) -> Path:
    """Render a ComicScript to PNG (or SVG fallback).

    ``subhead`` defaults to ``script.central_tension`` when not explicitly provided.
    Passes ``script.facts`` and ``script.source_name`` to the News Card header.
    """
    effective_subhead = subhead or script.central_tension or ""
    scenes   = build_scenes(script)
    svg_str  = _render_comic(
        script.headline,
        effective_subhead,
        scenes,
        facts       = list(script.facts) if script.facts else None,
        source_name = script.source_name or "",
    )
    uid      = run_id or uuid.uuid4().hex[:8].upper()
    out_path = Path(out_dir) / f"aifeeders_comic_{uid}.png"
    return _svg_to_png(svg_str, out_path)


def generate_comic(
    headline:          str,
    media_hook:        str,
    founder_line:      str,
    engineer_line:     str,
    skeptic_line:      str,
    policy_line:       str,
    audience_question: str,
    run_id:            Optional[str] = None,
    out_dir:           str = "/tmp",
    subhead:           str = "",
    host_question:     str = "",
) -> Path:
    """Backwards-compatible helper with fixed Founder/Engineer/Skeptic/Policy voices."""
    script = ComicScript(
        headline=headline, host_name="Maya", host_role="Media Host",
        news_brief=media_hook, central_tension="",
        host_question=host_question,
        cast=[
            PersonaCast("Arjun",  "FutureAI Founder",    "business", "💼"),
            PersonaCast("Steve",  "Enterprise Engineer",  "linkedin", "🧑‍💻"),
            PersonaCast("Nina",   "Generalist Mind",      "genz",     "⚖️"),
            PersonaCast("Daniel", "Policy Specialist",    "policy",   "🏛️"),
        ],
        voice1_line=founder_line, voice2_line=engineer_line,
        voice3_line=skeptic_line, voice4_line=policy_line,
        host_synthesis="Four perspectives — one story. What do you think?",
        audience_question=audience_question,
    )
    return generate_comic_from_script(script, run_id=run_id, out_dir=out_dir, subhead=subhead)


# ── Pipeline integration: build script from workflow objects ──────────────────

# Ensure host and cast names NEVER overlap in the same story
_HOST_NAMES = ["Maya", "Daniel", "Sophia", "Chloe", "Tara", "Julian"]
_CAST_NAMES: dict[str, list[str]] = {
    "business": ["Arjun", "Priya", "Leo", "Ravi", "Sarah", "David"],
    "linkedin": ["Steve", "Anika", "Tom", "Marcus", "Chris", "Felix"],
    "genz":     ["Nina",  "Zoe",   "Alex", "Jordan", "Sam",   "Taylor"],
    "policy":   ["James", "Rachel", "Michael", "Wei", "Laura", "Carlos"],
}
_ROLE_LABELS: dict[str, str] = {
    "business": "AI Infrastructure Founder",
    "linkedin": "ML Platform Engineer",
    "genz":     "AI Industry Analyst",
    "policy":   "AI Policy Lead",
}
_PERSONA_EMOJI: dict[str, str] = {
    "business": "💼", "linkedin": "🧑‍💻", "genz": "📊", "policy": "🏛️",
}


def _pick_name(persona: str, article_id: str, slot: int) -> str:
    import hashlib
    seed  = int(hashlib.md5(f"{article_id}:{persona}:{slot}".encode()).hexdigest(), 16)
    names = _CAST_NAMES.get(persona, ["Alex"])
    return names[seed % len(names)]


def _pick_host_name(article_id: str) -> str:
    import hashlib
    seed = int(hashlib.md5(f"host:{article_id}".encode()).hexdigest(), 16)
    return _HOST_NAMES[seed % len(_HOST_NAMES)]


def _clip_at_sentence_local(text: str, limit: int) -> str:
    """UTF-16 safe sentence clipper without importing PublisherAgent."""
    def _lk_len(t: str) -> int:
        return sum(2 if ord(c) > 0xFFFF else 1 for c in t)

    if _lk_len(text) <= limit:
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


def _extract_dynamic_tags(
    headline: str,
    summary: str,
    source: str,
    key_points: list[str],
    event_type: str,
    story_seo_tags: list[str] | None = None,
    max_tags: int = 4,
) -> list[str]:
    """
    SEO & AEO Dynamic Hashtag Extractor:
    Consolidates meaningful multi-word and entity hashtags (e.g. #Claude37Sonnet, #AIInfrastructure)
    instead of single-word fragments. Target 3–4 high-relevance tags.
    """
    import re
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
            cleaned = "#" + re.sub(r"[^A-Za-z0-9]", "", cleaned[1:])
            tag_lower = cleaned.lower()
            if len(cleaned) > 2 and tag_lower not in seen_lower:
                seen_lower.add(tag_lower)
                found_tags.append(cleaned)
            if len(found_tags) >= max_tags:
                return found_tags

    # 2. Extract compound entities (e.g., "Claude 3.7", "Anthropic AI", "DeepSeek R2")
    compound_patterns = [
        r"\b([A-Z][a-zA-Z]+(?:\s+[0-9]+(?:\.[0-9]+)*)?(?:\s+[A-Z][a-zA-Z]+)?)\b",
    ]
    for pat in compound_patterns:
        for match in re.findall(pat, headline):
            words = match.split()
            # If multi-token or single substantial entity like Anthropic/DeepSeek/Copilot
            if len(words) >= 2 or len(words[0]) >= 6:
                camel = "".join(w.capitalize() if not w.isupper() else w for w in words)
                camel_clean = re.sub(r"[^A-Za-z0-9]", "", camel)
                if len(camel_clean) >= 4 and camel_clean.lower() not in {"unveils", "announces", "launches", "releases"}:
                    tag = f"#{camel_clean}"
                    if tag.lower() not in seen_lower:
                        seen_lower.add(tag.lower())
                        found_tags.append(tag)
            if len(found_tags) >= 2:
                break

    # 3. Contextual Domain Tag
    _EVENT_AEO = {
        "product_launch": ["AIInfrastructure", "EnterpriseAI"],
        "funding":        ["AIVenture", "EnterpriseAI"],
        "regulation":     ["AIGovernance", "AIRegulation"],
        "research":       ["AIResearch", "MachineLearning"],
        "acquisition":    ["AIStrategy", "TechConsolidation"],
        "other":          ["AIInfrastructure", "AIGovernance"],
    }
    for ev_tag in _EVENT_AEO.get(event_type, ["AIInfrastructure"]):
        if len(found_tags) >= max_tags:
            break
        tag = f"#{ev_tag}"
        if tag.lower() not in seen_lower:
            seen_lower.add(tag.lower())
            found_tags.append(tag)

    return found_tags[:max_tags]


def _derive_facts_from_summary(summary) -> list[str]:
    """
    Extract 3–5 short fact-chip strings from the news summary for the News Card.

    Extraction priority:
      1. Intelligence judgment.facts (verified numbers/claims from Jev)
      2. Key points — first number-bearing clause from each point
      3. Headline — any numeric token as a last-resort chip

    Chip format: short uppercase, e.g. "$42B NET LOSS", "$4.6B REVENUE (12X)", "518B CLOUD CAPEX".
    Each chip should be ≤ 30 chars so it fits on one pill without truncation.
    """
    import re as _re_f

    chips: list[str] = []
    seen: set[str]   = set()

    def _add(text: str) -> None:
        t = text.strip().upper()[:30]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            chips.append(t)

    # 1. Verified facts from Jev judgment
    intel = getattr(summary, "intelligence", None)
    if intel is not None:
        judgment = (intel.get("judgment") if isinstance(intel, dict) else getattr(intel, "judgment", None))
        if judgment is not None:
            raw_facts = (judgment.get("facts") if isinstance(judgment, dict) else getattr(judgment, "facts", []))
            for f in (raw_facts or [])[:5]:
                f_str = str(f).strip()
                # Extract just the first clause (up to a comma or semicolon) to keep chips short
                short = _re_f.split(r"[,;—–]", f_str)[0].strip()
                if short and len(short) <= 40:
                    _add(short)

    # 2. Key points — extract numeric-bearing clauses
    for kp in (summary.key_points or [])[:5]:
        if len(chips) >= 5:
            break
        kp = str(kp).strip()
        # Look for a clause with a number / dollar / percent / multiplier
        numbers = _re_f.findall(r"\$?[\d,]+(?:\.\d+)?[BMKTx%×]?\b[\w\s]{0,20}", kp)
        for n in numbers[:1]:
            n = n.strip().rstrip(" .")
            if len(n) <= 32:
                _add(n)

    # 3. Headline fallback — extract any $N or NB/NM/NK tokens
    if len(chips) < 2:
        hl_tokens = _re_f.findall(r"\$[\d,.]+[BMK]?|\b\d+(?:\.\d+)?[BMKx%×]\b", summary.headline)
        for tok in hl_tokens[:3]:
            if len(chips) >= 5:
                break
            _add(tok)

    return chips[:5]




async def build_comic_script_from_summary(
    summary,
    personas,
    run_id: str | None = None,
) -> ComicScript:
    """Build a ComicScript from pipeline workflow objects — no extra LLM calls."""

    def _first_sentences(text: str, n: int = 2, limit: int = 200) -> str:
        text  = (text or "").strip().strip('"').strip()
        count = 0
        for i, c in enumerate(text):
            if c in ".!?" and i >= 15:
                count += 1
                if count >= n:
                    return _clip_at_sentence_local(text[:i + 1], limit)
        return _clip_at_sentence_local(text, limit)

    def _story_field(fname: str, fallback: str = "") -> str:
        story = getattr(summary, "story", None)
        if story is None:
            return fallback
        if isinstance(story, dict):
            return str(story.get(fname, fallback) or fallback).strip()
        return str(getattr(story, fname, fallback) or fallback).strip()

    aid     = summary.article_id
    opening = _story_field("media_host_opening")
    hook    = _story_field("hook") or summary.why_it_matters
    why     = summary.why_it_matters or ""
    what_changed   = _story_field("what_changed") or ""
    second_order_b = _story_field("second_order_effect") or ""
    # central_tension for the outside post hook: prefer media_host_opening (the conflict/tension hook),
    # fall back to future_question or media_host_audience_cta for the hand-off pill only.
    tension = _story_field("media_host_opening") or _story_field("future_question") or _story_field("media_host_audience_cta") or ""

    # ── Scene 1 brief: 4 lines — what happened · context · debate angle · host seed ──
    # Line 1: what actually changed (hook sentence from story or headline)
    # Line 2: why it matters to practitioners right now
    # Line 3: the deeper tension or second-order effect
    # Line 4: the question this debate is going to answer
    brief_parts: list[str] = []
    if opening:
        # opening may already contain 2-4 rich sentences
        opening_sents = [s.strip() for s in __import__("re").split(r"(?<=[.!?])\s+", opening.strip()) if len(s.strip()) >= 15]
        brief_parts = opening_sents[:4]
    else:
        if summary.headline:
            brief_parts.append(summary.headline.strip().rstrip(".") + ".")
        if why and why.lower() not in " ".join(brief_parts).lower():
            brief_parts.append(_first_sentences(why, n=1, limit=120))
        if what_changed and what_changed.lower() not in " ".join(brief_parts).lower():
            brief_parts.append(_first_sentences(what_changed, n=1, limit=110))
        if second_order_b and second_order_b.lower() not in " ".join(brief_parts).lower():
            brief_parts.append(_first_sentences(second_order_b, n=1, limit=100))

    # Ensure we have at least 2 and at most 4 lines; clip each for readability
    brief_parts = [_clip_at_sentence_local(p, 150) for p in brief_parts if p.strip()][:4]
    news_brief = " ".join(brief_parts).strip()

    # central_tension drives the hand-off pill — kept short
    central_tension = _first_sentences(tension, n=1, limit=110) if tension else ""

    host_name   = _pick_host_name(aid)
    voice_order = ["business", "linkedin", "genz", "policy"]
    cast        = [
        PersonaCast(
            name    = _pick_name(k, aid, i),
            role    = _ROLE_LABELS[k],
            persona = k,
            emoji   = _PERSONA_EMOJI[k],
        )
        for i, k in enumerate(voice_order)
    ]

    persona_map = {
        "business": personas.business,
        "linkedin": personas.linkedin,
        "genz":     personas.genz,
        "policy":   personas.policy,
    }

    def _voice(key: str, next_name: str = "") -> str:
        """
        Rich conversational bubble — 4 to 5 complete sentences so the reader
        fully understands the persona's position before the hand-off.

        Sentence 1 — core claim: specific, article-grounded, starts cold (no opener).
        Sentence 2 — consequence: practical impact or production reality.
        Sentence 3 — tension / second-order effect: what this actually means deeper down.
        Sentence 4 — stakes: who wins, who loses, or what changes in practice.
        Sentence 5 — hand-off: direct question to next speaker by name.

        Font auto-shrinks via _fit_text_to_box() so longer text still fits the bubble.
        Hard cap raised to 480 chars — enough for 4–5 sentences at min_font=14.
        """
        import re as _re2
        po = persona_map.get(key)
        if not po or not po.perspective:
            return ""
        text = po.perspective.strip().strip('"').strip()
        if not text:
            return ""

        raw = [s.strip() for s in _re2.split(r"(?<=[.!?])\s+", text) if len(s.strip()) >= 12]

        # Take up to 4 body sentences — gives the reader the full argument
        body: list[str] = []
        for s in raw:
            body.append(s)
            if len(body) == 4:
                break
        if not body:
            body = [raw[0]] if raw else [text[:150]]

        # Final sentence: explicit hand-off question to the next speaker by name.
        # Prefer next_question field; fall back to the last question in the perspective,
        # or the last sentence if no question exists.
        nq = (getattr(po, "next_question", "") or "").strip()
        if not nq:
            questions = [s for s in raw if s.endswith("?")]
            nq = questions[-1] if questions else (raw[-1] if raw else "")
        # Remove nq from body if it's already there to avoid duplication
        body = [s for s in body if s != nq][:4]
        # Personalise with next speaker's name when not already present
        if next_name and next_name not in nq:
            nq = f"{next_name}, {nq[0].lower()}{nq[1:]}" if nq else f"{next_name}?"

        kept = body + ([nq] if nq else [])
        result = " ".join(kept[:5])
        # Raised cap: 480 chars fits 4-5 sentences; _fit_text_to_box handles rendering
        return _clip_at_sentence_local(result, 480)

    def _next_q(key: str) -> str:
        """Return the explicit next_question from PersonaOutput, or empty string."""
        po = persona_map.get(key)
        if po and getattr(po, "next_question", ""):
            return po.next_question.strip()
        return ""

    # Pass each persona's next speaker name so Sentence 3 directly addresses them by name
    # Order: business → linkedin → genz → policy → (host closes)
    voice1 = _voice("business", next_name=cast[1].name)   # → Engineer
    voice2 = _voice("linkedin", next_name=cast[2].name)   # → Analyst
    voice3 = _voice("genz",     next_name=cast[3].name)   # → Policy Lead
    voice4 = _voice("policy",   next_name="")              # → Host closes; no hand-off needed

    cta = _story_field("media_host_audience_cta") or _story_field("future_question") or ""

    # ── Scene 6 synthesis: use story analytical fields, not cast-name template ──
    media_synth  = _story_field("media_host_synthesis")
    perspective  = _story_field("perspective")
    second_order = _story_field("second_order_effect")
    future_q     = _story_field("future_question")
    synthesis_raw = media_synth or " ".join(filter(None, [perspective, second_order, future_q])).strip()
    # Scene 6 synthesis: keep up to 3 sentences so the host wrap-up reads as a
    # proper conclusion, not just a one-liner.  Cap raised to 420 chars.
    host_synthesis = (
        _clip_at_sentence_local(synthesis_raw, 420)
        if synthesis_raw
        else (
            f"Four perspectives, one unresolved question: "
            f"{central_tension or 'can organisations move as fast as AI enables?'} "
            f"Ravi sees opportunity, Tom sees operational friction, Taylor sees regulatory exposure, "
            f"and Laura sees a governance gap — all triggered by the same announcement."
        )
    )

    # Audience question: use story CTA when available, otherwise build a labelled
    # choice block from the central tension. Labelled choices (emoji + letter) lower
    # the barrier to reply and signal which dimension each choice represents.
    if cta:
        audience_question = _clip_at_sentence_local(cta, 300)
    else:
        subject = (central_tension or summary.headline)[:60].rstrip("?. ")
        audience_question = (
            f"If you were responsible for deploying this in your environment, what becomes the biggest bottleneck first?\n\n"
            f"⚡ A — Latency & system SLAs\n"
            f"💰 B — Unit cost & token economics\n"
            f"🔍 C — Observability & debugging\n"
            f"🛡️ D — Governance & compliance\n\n"
            f"Pick one + tell us what you've seen in your own environment. 👇"
        )

    # ── Dynamic hashtags from the existing engine — no hardcoded list ──────────
    intel = getattr(summary, "intelligence", None)

    # event_type lives in the NewsIntelligence backbone, NOT on NewsSummary directly.
    # Correct lookup: summary.intelligence.event_type (Pydantic) or dict key.
    def _intel_val(key: str, default: str = "") -> str:
        if intel is None:
            return default
        if isinstance(intel, dict):
            return str(intel.get(key, default) or default)
        return str(getattr(intel, key, default) or default)

    event_type_for_tags = _intel_val("event_type", "other").lower().strip() or "other"

    story_seo_tags: list[str] = []
    if intel is not None:
        # dynamic_seo_hashtags is on NewsStory, nested under intelligence.story
        story_obj = (intel.get("story") if isinstance(intel, dict) else getattr(intel, "story", None))
        if story_obj is not None:
            raw_tags = (story_obj.get("dynamic_seo_hashtags") if isinstance(story_obj, dict)
                        else getattr(story_obj, "dynamic_seo_hashtags", None))
            if raw_tags:
                story_seo_tags = list(raw_tags)

    hashtags = _extract_dynamic_tags(
        headline       = summary.headline,
        summary        = summary.summary or "",
        source         = getattr(summary, "source", "") or "",
        key_points     = list(summary.key_points or []),
        event_type     = event_type_for_tags,
        story_seo_tags = story_seo_tags,
        max_tags       = 4,
    )
    if not hashtags:
        hashtags = ["#AIInfrastructure", "#EnterpriseAI", "#AIGovernance"]

    # ── News Card facts — key numbers/claims rendered as yellow chips ──────────
    # Derived deterministically from key_points and intelligence signals.
    # Format: short uppercase strings, max 5, e.g. "$42B NET LOSS".
    facts = _derive_facts_from_summary(summary)

    return ComicScript(
        headline          = summary.headline,
        host_name         = host_name,
        host_role         = "Media Host",
        news_brief        = news_brief,
        central_tension   = central_tension,
        cast              = cast,
        voice1_line       = voice1,
        voice2_line       = voice2,
        voice3_line       = voice3,
        voice4_line       = voice4,
        host_synthesis    = host_synthesis,
        audience_question = audience_question,
        # Causal question bridges — from PersonaOutput.next_question
        question1         = central_tension or "",   # host → A: the debate seed
        question2         = _next_q("business"),     # A (business) → B (linkedin)
        question3         = _next_q("linkedin"),     # B (linkedin) → C (genz)
        question4         = _next_q("genz"),         # C (genz)     → D (policy)
        hashtags          = hashtags,
        source_url        = getattr(summary, "source_url", ""),
        source_name       = getattr(summary, "source", ""),
        facts             = facts,
    )


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    path = generate_comic(
        headline       = "DeepSeek R2 beats GPT-4 at 10% of the compute cost",
        subhead        = "Why every AI cost model in enterprise just got rewritten",
        host_question  = "Arjun, does compute this cheap actually change how fast you ship?",
        media_hook     = (
            "DeepSeek's new model matches GPT-4 on benchmarks at a fraction of the compute cost. "
            "That reopens a question everyone thought was settled: does frontier AI actually need "
            "frontier spending?"
        ),
        founder_line   = (
            "If a model this cheap performs this well, my roadmap just got 10x more experiments "
            "for the same burn. But Steve, can enterprise teams actually move that fast?"
        ),
        engineer_line  = (
            "Speed isn't the constraint — production controls are. We still need evals, "
            "monitoring, and a fallback path before this touches live traffic. "
            "Nina, doesn't benchmark parity worry you though?"
        ),
        skeptic_line   = (
            "It does. Benchmark parity isn't production parity — we've been fooled by leaderboard "
            "wins before. Daniel, what does this do to the economics everyone's betting on?"
        ),
        policy_line    = (
            "It reshuffles them fast. A model this cheap running on foreign open-weights raises "
            "data-residency questions nobody's answered yet. The real risk isn't performance — "
            "it's trust."
        ),
        audience_question = (
            "DeepSeek R2 at 10% cost. What breaks first?\n\n"
            "A — Pricing power of US labs\n"
            "B — Enterprise trust in open-weights\n"
            "C — GPU investment thesis\n\n"
            "Where do you stand? Drop your take below 👇"
        ),
        run_id  = "TEST",
        out_dir = "/tmp",
    )
    print(f"Comic generated: {path}")
