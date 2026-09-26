"""
Comic Strip Generator for AIFeeders — 6-Scene Conversational Story Format.
v2 — redesigned for LinkedIn readability:
  • Big, bold headline (top header bar)
  • Small FACE avatars only (no full bodies) — content owns the panel
  • Name + Role shown as a bold header row right beside the face — unmissable
  • Tight padding everywhere — no dead space inside panels
  • "↳ replying to X" micro-tag so the conversation thread stays legible

STORY GRAMMAR (fixed narrative logic, dynamic cast):
─────────────────────────────────────────────────────
  Scene 1  MEDIA HOST — news brief + introduces the 4 named personas
  Scene 2  Persona A  — opens the debate (opportunity angle)
  Scene 3  Persona B  — replies to A (production-reality pushback)
  Scene 4  Persona C  — replies to B (second perspective)
  Scene 5  Persona D  — replies to C (second-order effect / governance)
  Scene 6  MEDIA HOST — synthesises + poses the audience question

OUTSIDE the image (LinkedIn post text):
  🔗 Source: <name> — <url>                (only if available)

  🎙️ <HostName> — To the Audience:
  <audience_question>

  Where do you stand? Drop your take below 👇

  ⚠️ Perspectives are AI-simulated — not professional advice.
  🤖 AIFeeders · Daily AI Intelligence · Powered by Jev

  #Dynamic #Hashtags

Layout: 3 columns × 2 rows.
SVG → PNG via cairosvg → rsvg-convert → inkscape → .svg fallback.
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
HEADER_H = 148        # brand row (48px) + divider + headline (2×34px) + subhead (22px) = 148

TOTAL_W = PANEL_W * COLS                 # 1440
TOTAL_H = PANEL_H * ROWS + HEADER_H     # 888

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
    "genz": "⚖️", "policy": "🏛️",
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

def _face(cx: int, cy: int, persona: str, r: int = 26) -> str:
    accent = ACCENT.get(persona, "#555555")
    hair   = _HAIR.get(persona, "#3D2314")
    badge  = _BADGE.get(persona, "🙂")
    br     = max(11, int(r * 0.42))

    return f"""<g transform="translate({cx},{cy})">
<circle cx="0" cy="0" r="{r}" fill="{SKIN}" stroke="{accent}" stroke-width="3.5"/>
<path d="M{-r+2},{-r*0.35:.1f} Q0,{-r*1.55:.1f} {r-2},{-r*0.35:.1f} Q{r-4},{-r*0.65:.1f} 0,{-r*0.7:.1f} Q{-r+4},{-r*0.65:.1f} {-r+2},{-r*0.35:.1f} Z" fill="{hair}"/>
<circle cx="{-r*0.32:.1f}" cy="{-r*0.05:.1f}" r="{max(2, int(r*0.09))}" fill="{BORDER_COL}"/>
<circle cx="{r*0.32:.1f}"  cy="{-r*0.05:.1f}" r="{max(2, int(r*0.09))}" fill="{BORDER_COL}"/>
<path d="M{-r*0.28:.1f},{r*0.35:.1f} Q0,{r*0.55:.1f} {r*0.28:.1f},{r*0.35:.1f}" stroke="{BORDER_COL}" stroke-width="2" fill="none"/>
<circle cx="{r*0.72:.1f}" cy="{r*0.72:.1f}" r="{br}" fill="#FFFFFF" stroke="{accent}" stroke-width="2"/>
<text x="{r*0.72:.1f}" y="{r*0.72 + br*0.38:.1f}" text-anchor="middle" font-size="{br*1.15:.1f}">{badge}</text>
</g>"""


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
) -> str:
    """Speech bubble that FILLS the given box — text auto-scales, tail points up to speaker."""
    rx_ = 12
    svg = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx_}" '
           f'fill="{BUBBLE_BG}" stroke="{accent}" stroke-width="2.5"/>\n')

    tx = x + int(w * tail_x_frac)
    svg += (f'<polygon points="{tx},{y} {tx-9},{y-16} {tx+17},{y}" '
            f'fill="{BUBBLE_BG}" stroke="{accent}" stroke-width="2.5"/>\n')
    svg += (f'<line x1="{tx-8}" y1="{y+1}" x2="{tx+16}" y2="{y+1}" '
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
                       prev_line: str, accent: str, is_close: bool = False) -> tuple[str, int]:
    """
    Compact tinted strip: tiny face + bold label + quoted question/line.
    Shows exactly WHO this panel is responding to and WHAT they said.
    Returns (svg, strip_height).
    """
    h  = 50
    fr = 16
    # Truncate quote to fit on one line — keep it punchy
    quote = prev_line.strip().strip('"')
    max_chars = 62
    if len(quote) > max_chars:
        quote = quote[:max_chars].rsplit(" ", 1)[0] + "…"

    label = "» RESPONDING TO" if is_close else "» ANSWERING"
    suffix = "" if is_close else "'S QUESTION"

    # Tinted background pill
    svg = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" '
           f'fill="{accent}" opacity="0.08" stroke="{accent}" stroke-width="1.5" '
           f'stroke-opacity="0.30"/>\n')
    # Vertical connector to bubble below
    svg += (f'<line x1="{x + 26}" y1="{y + h}" x2="{x + 26}" y2="{y + h + 8}" '
            f'stroke="{accent}" stroke-width="2.5" opacity="0.45"/>\n')

    fcx, fcy = x + 20, y + h // 2
    svg += _face(fcx, fcy, prev_persona, r=fr)

    tx = x + fr * 2 + 16
    # "» ANSWERING MAYA'S QUESTION" — bold, accent colour, 13px
    svg += (f'<text x="{tx}" y="{y + 19}" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="13" font-weight="bold" fill="{accent}">'
            f'{label} {html.escape(prev_name.upper())}{suffix}</text>\n')
    # The actual quoted line — italic, muted, 14px so it's readable
    svg += (f'<text x="{tx}" y="{y + 38}" font-family="Georgia,Arial,sans-serif" '
            f'font-size="14" font-style="italic" fill="{MUTED_TEXT}">'
            f'"{html.escape(quote)}"</text>\n')
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

def _panel_frame(px: int, py: int, accent: str) -> str:
    return (f'<rect x="{px + BORDER}" y="{py + BORDER}" '
            f'width="{PANEL_W - BORDER * 2}" height="{PANEL_H - BORDER * 2}" '
            f'fill="{PANEL_BG}" stroke="{BORDER_COL}" stroke-width="{BORDER}" rx="8"/>\n'
            # Top accent stripe — 7px tall, signals speaker colour at a glance
            f'<rect x="{px + BORDER}" y="{py + BORDER}" '
            f'width="{PANEL_W - BORDER * 2}" height="7" rx="3" fill="{accent}"/>\n')


def _render_scene1(px: int, py: int, scene: ComicScene) -> str:
    """Scene 1 — host brief (left) + hand-off question pill + 4-row cast roster (right)."""
    c   = ACCENT.get(scene.persona, "#2563EB")
    svg = _panel_frame(px, py, c)

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


def _render_conversation_scene(px: int, py: int, scene: ComicScene) -> str:
    """Scenes 2-6 — name row + quote-reply strip + big speech bubble."""
    spk_c = ACCENT.get(scene.persona, "#555555")
    svg   = _panel_frame(px, py, spk_c)

    hx = px + BORDER + 10
    hy = py + BORDER + 12
    row_svg, row_h = _name_row(hx, hy, scene.name, scene.role, scene.persona, face_r=26)
    svg += row_svg

    cursor_y = hy + row_h + 6

    # Quote-reply strip — shows exactly what question/line is being answered
    strip_h = 0
    if scene.prev_name and scene.prev_line:
        strip_x = px + BORDER + 8
        strip_w = PANEL_W - BORDER * 2 - 16
        strip_svg, strip_h = _quote_reply_strip(
            strip_x, cursor_y, strip_w,
            scene.prev_persona, scene.prev_name, scene.prev_line,
            spk_c, is_close=scene.is_close,
        )
        svg += strip_svg
        cursor_y += strip_h + 8

    bubble_x = px + BORDER + 8
    bubble_w = PANEL_W - BORDER * 2 - 16
    bubble_h = PANEL_H - (cursor_y - py) - 12
    svg += _speech_bubble(bubble_x, cursor_y, bubble_w, bubble_h, scene.text,
                          tail_x_frac=0.10, accent=spk_c, max_font=24, min_font=14)
    return svg


def _render_scene(px: int, py: int, scene: ComicScene) -> str:
    if scene.is_intro:
        return _render_scene1(px, py, scene)
    return _render_conversation_scene(px, py, scene)


# ── Full comic strip render ───────────────────────────────────────────────────

def _render_comic(headline: str, subhead: str, scenes: list[ComicScene],
                  brand: str = "AIFeeders") -> str:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'width="{TOTAL_W}" height="{TOTAL_H}" viewBox="0 0 {TOTAL_W} {TOTAL_H}">\n')
    svg += f'<rect width="{TOTAL_W}" height="{TOTAL_H}" fill="{BG}"/>\n'

    # ── Header dark background ─────────────────────────────────────────────────
    svg += f'<rect x="0" y="0" width="{TOTAL_W}" height="{HEADER_H}" fill="{HEADER_BG}"/>\n'
    # Left accent stripe
    svg += f'<rect x="0" y="0" width="8" height="{HEADER_H}" fill="#2563EB"/>\n'

    # ── BRAND ROW — occupies top 48px ─────────────────────────────────────────
    # Blue badge with brain emoji
    svg += f'<rect x="18" y="10" width="40" height="40" rx="8" fill="#2563EB"/>\n'
    svg += (f'<text x="38" y="35" text-anchor="middle" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="22" fill="#FFFFFF">🧠</text>\n')
    # AIFEEDERS — 26px bold white, unmissable
    svg += (f'<text x="68" y="38" font-family="Arial Black,Arial,Helvetica,sans-serif" '
            f'font-size="26" font-weight="900" letter-spacing="2.5" fill="#FFFFFF">'
            f'{brand.upper()}</text>\n')
    # Tagline — 16px bold accent blue, on the same baseline
    # Approximate brand text width: 26px * 0.72 avg char width * letter-spacing ≈ 20px/char
    brand_px = 68 + len(brand) * 20 + 16
    svg += (f'<text x="{brand_px}" y="38" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="16" font-weight="bold" letter-spacing="0.8" fill="#60A5FA">'
            f'·  ONE NEWS.  MULTIPLE REAL-WORLD VOICES.</text>\n')

    # Divider between brand row and headline
    svg += (f'<line x1="18" y1="56" x2="{TOTAL_W - 18}" y2="56" '
            f'stroke="#2563EB" stroke-width="1.5" opacity="0.35"/>\n')

    # ── HEADLINE — 34px bold, max 2 lines, cols=50 to avoid mid-phrase breaks ─
    hl_lines = _wrap(headline, cols=50)[:2]
    hl_fs    = 34 if len(hl_lines) == 1 else 28
    hl_lh    = hl_fs + 8
    for i, ln in enumerate(hl_lines):
        svg += (f'<text x="18" y="{72 + i * hl_lh}" '
                f'font-family="Arial Black,Arial,Helvetica,sans-serif" '
                f'font-size="{hl_fs}" font-weight="900" fill="{HEADER_TEXT}">'
                f'{html.escape(ln)}</text>\n')

    # ── SUBHEAD — 18px, accent colour so it reads clearly on dark bg ──────────
    if subhead:
        sub_y = 72 + len(hl_lines) * hl_lh + 6
        svg += (f'<text x="18" y="{min(sub_y, HEADER_H - 8)}" '
                f'font-family="Arial,Helvetica,sans-serif" '
                f'font-size="18" fill="#FCD34D">'   # warm yellow — pops on dark bg
                f'{html.escape(subhead[:115])}</text>\n')

    for i, scene in enumerate(scenes[:6]):
        col, row = i % COLS, i // COLS
        px, py   = col * PANEL_W, HEADER_H + row * PANEL_H
        svg += _render_scene(px, py, scene)

    svg += "</svg>\n"
    return svg


# ── SVG → PNG conversion ──────────────────────────────────────────────────────

def _svg_to_png(svg_str: str, out_path: Path) -> Path:
    import subprocess
    svg_path = out_path.with_suffix(".svg")
    svg_path.write_text(svg_str, encoding="utf-8")

    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(url=str(svg_path), write_to=str(out_path),
                         output_width=TOTAL_W, output_height=TOTAL_H)
        svg_path.unlink(missing_ok=True)
        return out_path
    except (ImportError, OSError):
        pass

    for cmd in (["rsvg-convert", "-o", str(out_path), str(svg_path)],
                ["inkscape", "--export-type=png", f"--export-filename={out_path}", str(svg_path)]):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=20)
            if r.returncode == 0:
                svg_path.unlink(missing_ok=True)
                return out_path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    logger.warning("No SVG→PNG converter found. Returning .svg path.")
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
    hashtags:          list[str] = field(default_factory=list)
    source_url:        str = ""
    source_name:       str = ""
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

    # Each persona answers the QUESTION from the previous speaker
    scene2 = _conv(A, host_cast,   script.voice1_line, prev_line=host_q)
    scene3 = _conv(B, A,           script.voice2_line, prev_line=_extract_question(script.voice1_line))
    scene4 = _conv(C, B,           script.voice3_line, prev_line=_extract_question(script.voice2_line))
    scene5 = _conv(D, C,           script.voice4_line, prev_line=_extract_question(script.voice3_line))
    scene6 = _conv(host_cast, D,   script.host_synthesis,
                   prev_line=_extract_question(script.voice4_line), close=True)

    return [scene1, scene2, scene3, scene4, scene5, scene6]


# ── Outside-image LinkedIn post text ─────────────────────────────────────────

def build_outside_post(script: ComicScript) -> str:
    """
    Build the text block that goes OUTSIDE the image in the LinkedIn post.

    Format:
      📰 Source: <name>          (always shown when available, skipped silently if missing)
      🔗 <url>                   (always shown when available)
                                  (blank line only if either source field was shown)

      🎙️ <HostName> — To the Audience:

      <audience_question>

      Where do you stand? Drop your take below 👇   (appended if not already in question)

      ⚠️ Perspectives are AI-simulated — not professional advice.
      🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev

      #Hashtag1 #Hashtag2 …
    """
    lines: list[str] = []

    # Source block — always include whatever is available
    source_shown = False
    if script.source_name:
        lines.append(f"📰 Source: {script.source_name}")
        source_shown = True
    if script.source_url:
        lines.append(f"🔗 {script.source_url}")
        source_shown = True
    if source_shown:
        lines.append("")

    # Host CTA
    lines.append(f"🎙️ {script.host_name} — To the Audience:")
    lines.append("")
    lines.append(script.audience_question)

    # Always append the engagement CTA if it's not already in the question text
    cta_lower = script.audience_question.lower()
    if "where do you stand" not in cta_lower and "drop your" not in cta_lower:
        lines += ["", "Where do you stand? Drop your take below 👇"]

    # Always include the disclaimer + branding footer
    lines += [
        "",
        "⚠️ Perspectives are AI-simulated — not professional advice.",
        "🤖 AIFeeders  ·  Daily AI Intelligence  ·  Powered by Jev",
    ]

    if script.hashtags:
        lines += ["", " ".join(script.hashtags)]

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
    """
    effective_subhead = subhead or script.central_tension or ""
    scenes   = build_scenes(script)
    svg_str  = _render_comic(script.headline, effective_subhead, scenes)
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

_HOST_NAMES = ["Maya", "Daniel", "Sophia", "Marcus", "Elena", "Jordan"]
_CAST_NAMES: dict[str, list[str]] = {
    "business": ["Arjun", "Priya", "Leo", "Ravi", "Sarah", "David"],
    "linkedin": ["Steve", "Anika", "Tom", "Elena", "Marcus", "Chris"],
    "genz":     ["Nina",  "Zoe",   "Alex", "Jordan", "Sam",   "Taylor"],
    "policy":   ["James", "Rachel", "Michael", "Wei", "Laura", "Carlos"],
}
_ROLE_LABELS: dict[str, str] = {
    "business": "AI Founder",
    "linkedin": "Enterprise Engineer",
    "genz":     "Generalist Thinker",
    "policy":   "Policy Specialist",
}
_PERSONA_EMOJI: dict[str, str] = {
    "business": "💼", "linkedin": "🧑‍💻", "genz": "⚖️", "policy": "🏛️",
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


async def build_comic_script_from_summary(
    summary,
    personas,
    run_id: str | None = None,
) -> ComicScript:
    """Build a ComicScript from pipeline workflow objects — no extra LLM calls."""
    from daily_news.agents.publisher_agent import _clip_at_sentence

    def _first_sentences(text: str, n: int = 2, limit: int = 200) -> str:
        text  = (text or "").strip().strip('"').strip()
        count = 0
        for i, c in enumerate(text):
            if c in ".!?" and i >= 15:
                count += 1
                if count >= n:
                    return _clip_at_sentence(text[:i + 1], limit)
        return _clip_at_sentence(text, limit)

    def _story_field(fname: str, fallback: str = "") -> str:
        story = getattr(summary, "story", None)
        if story is None:
            return fallback
        if isinstance(story, dict):
            return str(story.get(fname, fallback) or fallback).strip()
        return str(getattr(story, fname, fallback) or fallback).strip()

    aid     = summary.article_id
    opening = _story_field("media_host_opening") or summary.headline
    hook    = _story_field("hook") or summary.why_it_matters
    why     = summary.why_it_matters or ""
    tension = _story_field("future_question") or _story_field("media_host_audience_cta") or ""

    news_brief = _first_sentences(opening, n=1, limit=120)
    if hook and hook.lower() not in news_brief.lower():
        news_brief += " " + _clip_at_sentence(hook, 100)
    if why and len(news_brief) < 200:
        news_brief += " " + _clip_at_sentence(why, 90)
    news_brief      = news_brief.strip()
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

    def _voice(key: str, n: int = 2) -> str:
        po = persona_map.get(key)
        return _first_sentences(po.perspective if po else "", n=n, limit=160)

    voice1, voice2, voice3, voice4 = (_voice(k) for k in voice_order)

    cta = _story_field("media_host_audience_cta") or _story_field("future_question") or ""
    host_synthesis = (
        f"{cast[0].name} sees opportunity. {cast[1].name} flags production complexity. "
        f"{cast[2].name} questions the broader impact. {cast[3].name} sees a governance gap. "
        + (_first_sentences(central_tension, n=1, limit=90) if central_tension
           else "So: can organisations move as fast as AI enables?")
    )

    audience_question = _clip_at_sentence(cta, 300) if cta else (
        f"If AI dramatically changes {summary.headline[:50]}…\n\n"
        f"A — Development speed\nB — Security and compliance\n"
        f"C — Governance and oversight\nD — Operational cost\nE — Something else entirely"
    )

    hashtags = ["#AI", "#GenerativeAI", "#AIFeeders", "#EnterpriseAI", "#FutureOfWork"]

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
        hashtags          = hashtags,
        source_url        = getattr(summary, "source_url", ""),
        source_name       = getattr(summary, "source", ""),
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
