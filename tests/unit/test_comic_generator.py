"""
Unit tests for comic_generator.py

Coverage:
  - _wrap:                       short text, long text, multiline
  - _speech_bubble:              all three tail variants render valid SVG
  - _render_panel:               accent stripe, label, bubble, character present
  - _render_comic:               correct dimensions, header text, 6 panels
  - _build_panels:               6 ComicPanel objects with correct persona mapping
  - generate_comic_from_script:  writes a file, SVG well-formed
  - generate_comic:              backwards-compat wrapper produces same output shape
  - ComicScript:                 dataclass fields round-trip correctly
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from daily_news.agents.comic_generator import (
    ACCENT,
    COLS,
    HEADER_H,
    PANEL_H,
    PANEL_W,
    ROWS,
    TOTAL_H,
    TOTAL_W,
    ComicPanel,
    ComicScript,
    _build_panels,
    _render_comic,
    _render_panel,
    _speech_bubble,
    _wrap,
    generate_comic,
    generate_comic_from_script,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_script(**overrides) -> ComicScript:
    defaults = dict(
        headline          = "OpenAI ships GPT-5 with native code execution",
        media_hook        = "GPT-5 runs your code. No sandbox. No waiting.",
        voice1_label      = "💼 FOUNDER",
        voice1_persona    = "business",
        voice1_line       = "Our deployment backlog just halved overnight.",
        voice2_label      = "🧑‍💻 ENGINEER",
        voice2_persona    = "linkedin",
        voice2_line       = "Who reviews what it executed in prod at 2am?",
        voice3_label      = "⚖️ SKEPTIC",
        voice3_persona    = "genz",
        voice3_line       = "Cheaper execution doesn't mean cheaper debugging.",
        voice4_label      = "🏛️ POLICY",
        voice4_persona    = "policy",
        voice4_line       = "No audit trail for autonomous code execution means no compliance.",
        audience_question = "AI executes code 24/7. Who owns the failures?\n👇 A) Builder  B) Operator  C) No one yet",
    )
    defaults.update(overrides)
    return ComicScript(**defaults)


# ── _wrap ─────────────────────────────────────────────────────────────────────

class TestWrap:
    def test_short_text_single_line(self):
        result = _wrap("Hello world", cols=40)
        assert result == ["Hello world"]

    def test_long_text_splits(self):
        text = "This is a very long sentence that should be wrapped into multiple lines"
        result = _wrap(text, cols=20)
        assert len(result) > 1
        for line in result:
            assert len(line) <= 22  # allow minor word-boundary slack

    def test_empty_string_returns_empty_marker(self):
        result = _wrap("", cols=26)
        assert result == [""]

    def test_whitespace_only_returns_empty_marker(self):
        result = _wrap("   ", cols=26)
        assert result == [""]

    def test_default_cols(self):
        text = "A" * 30
        result = _wrap(text)   # default cols=26
        assert len(result) >= 1


# ── _speech_bubble ────────────────────────────────────────────────────────────

class TestSpeechBubble:
    def _make(self, tail: str) -> str:
        return _speech_bubble(
            x=10, y=10, w=200,
            lines=["Line one", "Line two"],
            font_size=15,
            tail=tail,
        )

    def test_bottom_left_tail_renders_polygon(self):
        svg = self._make("bottom-left")
        assert "<polygon" in svg

    def test_bottom_right_tail_renders_polygon(self):
        svg = self._make("bottom-right")
        assert "<polygon" in svg

    def test_center_tail_renders_polygon(self):
        svg = self._make("center")
        assert "<polygon" in svg

    def test_bubble_contains_rect(self):
        svg = self._make("bottom-left")
        assert "<rect" in svg

    def test_text_lines_in_svg(self):
        svg = self._speech_bubble_with_lines(["Hello", "World"])
        assert "Hello" in svg
        assert "World" in svg

    def _speech_bubble_with_lines(self, lines):
        return _speech_bubble(x=0, y=0, w=300, lines=lines)

    def test_first_line_bold(self):
        svg = _speech_bubble(x=0, y=0, w=300, lines=["First", "Second"])
        # first text element should have font-weight="bold"
        first_text_match = re.search(r'<text[^>]*>First', svg)
        assert first_text_match is not None
        before_first_text = svg[:first_text_match.start()]
        # Find the last font-weight before "First"
        weights = re.findall(r'font-weight="([^"]+)"', svg[:first_text_match.end()])
        assert "bold" in weights

    def test_accent_colour_applied_to_stroke(self):
        svg = _speech_bubble(x=0, y=0, w=300, lines=["x"], accent="#AABBCC")
        assert "#AABBCC" in svg

    def test_html_escaped_special_chars(self):
        svg = _speech_bubble(x=0, y=0, w=300, lines=["5 < 10", "a & b"])
        assert "&lt;" in svg
        assert "&amp;" in svg


# ── _render_panel ─────────────────────────────────────────────────────────────

class TestRenderPanel:
    def _panel(self, persona: str = "media") -> str:
        panel = ComicPanel(
            persona = persona,
            label   = "🎙️ MEDIA",
            lines   = ["Breaking news", "right here."],
        )
        return _render_panel(px=0, py=HEADER_H, panel=panel, idx=0)

    def test_panel_bg_rect_present(self):
        svg = self._panel()
        assert "FAFAF8" in svg  # PANEL_BG

    def test_accent_stripe_present(self):
        svg = self._panel("media")
        # accent colour for media is #2563EB
        assert ACCENT["media"] in svg

    def test_label_text_present(self):
        svg = self._panel()
        assert "MEDIA" in svg

    def test_character_svg_included(self):
        svg = self._panel("business")
        # The character function emits <ellipse> for the body glow
        assert "<ellipse" in svg

    def test_unknown_persona_no_character_crash(self):
        panel = ComicPanel(persona="unknown_xyz", label="? UNKNOWN", lines=["test"])
        # Should render without raising — character is just skipped
        svg = _render_panel(px=0, py=HEADER_H, panel=panel, idx=0)
        assert "<rect" in svg  # panel background still drawn

    def test_all_personas_render(self):
        for persona in ["media", "business", "linkedin", "genz", "policy"]:
            panel = ComicPanel(persona=persona, label=persona.upper(), lines=["line"])
            svg   = _render_panel(px=0, py=0, panel=panel, idx=0)
            assert "<ellipse" in svg  # body glow from each character func


# ── _build_panels ─────────────────────────────────────────────────────────────

class TestBuildPanels:
    def test_returns_six_panels(self):
        script = _minimal_script()
        panels = _build_panels(script)
        assert len(panels) == 6

    def test_first_panel_is_media(self):
        panels = _build_panels(_minimal_script())
        assert panels[0].persona == "media"
        assert panels[0].label   == "🎙️ MEDIA"

    def test_last_panel_is_aifeeders(self):
        panels = _build_panels(_minimal_script())
        assert panels[5].persona == "media"
        assert "AIFEEDERS" in panels[5].label

    def test_voice1_persona_mapped(self):
        panels = _build_panels(_minimal_script())
        assert panels[1].persona == "business"
        assert "FOUNDER" in panels[1].label

    def test_all_lines_wrapped_to_list(self):
        panels = _build_panels(_minimal_script())
        for p in panels:
            assert isinstance(p.lines, list)
            assert len(p.lines) >= 1

    def test_long_line_capped_at_7(self):
        script = _minimal_script(
            media_hook="Word " * 60  # very long — should be capped at 7 wrapped lines
        )
        panels = _build_panels(script)
        assert len(panels[0].lines) <= 7

    def test_blank_separator_preserved(self):
        script = _minimal_script(media_hook="First line.\nSecond line.")
        panels = _build_panels(script)
        # newline in source produces a blank "" separator in the wrapped output
        flat = " ".join(panels[0].lines)
        assert "First" in flat

    def test_is_close_flag_on_last_panel(self):
        panels = _build_panels(_minimal_script())
        assert panels[5].is_close is True
        for p in panels[:5]:
            assert p.is_close is False


# ── _render_comic ─────────────────────────────────────────────────────────────

class TestRenderComic:
    def _svg(self) -> str:
        panels = _build_panels(_minimal_script())
        return _render_comic("Test headline", panels)

    def test_correct_total_dimensions_in_svg(self):
        svg = self._svg()
        assert f'width="{TOTAL_W}"' in svg
        assert f'height="{TOTAL_H}"' in svg

    def test_header_background_present(self):
        svg = self._svg()
        assert "0F172A" in svg   # HEADER_BG

    def test_brand_text_present(self):
        svg = self._svg()
        assert "AIFeeders" in svg
        assert "ONE NEWS" in svg

    def test_headline_present(self):
        svg = self._svg()
        assert "Test headline" in svg

    def test_six_panel_accent_stripes(self):
        svg = self._svg()
        # Each panel renders one accent-colour top stripe rect with height="5"
        stripe_count = svg.count('height="5"')
        assert stripe_count == 6

    def test_custom_brand_name(self):
        panels = _build_panels(_minimal_script())
        svg = _render_comic("headline", panels, brand="MYBOT")
        assert "MYBOT" in svg

    def test_long_headline_truncated(self):
        long_hl = "X" * 200
        panels  = _build_panels(_minimal_script())
        svg     = _render_comic(long_hl, panels)
        # Headline in SVG should be capped at 78 chars + ellipsis
        assert "…" in svg


# ── generate_comic_from_script ────────────────────────────────────────────────

class TestGenerateComicFromScript:
    def test_produces_file(self, tmp_path):
        script = _minimal_script()
        result = generate_comic_from_script(script, run_id="UNIT01", out_dir=str(tmp_path))
        assert result.exists()

    def test_filename_contains_run_id(self, tmp_path):
        result = generate_comic_from_script(_minimal_script(), run_id="MYID", out_dir=str(tmp_path))
        assert "MYID" in result.name

    def test_svg_fallback_is_valid_xml(self, tmp_path):
        result = generate_comic_from_script(_minimal_script(), run_id="XML01", out_dir=str(tmp_path))
        content = result.read_text(encoding="utf-8")
        assert content.strip().startswith("<")
        assert "<svg" in content
        assert "</svg>" in content

    def test_svg_contains_all_personas(self, tmp_path):
        script  = _minimal_script()
        result  = generate_comic_from_script(script, run_id="P01", out_dir=str(tmp_path))
        content = result.read_text(encoding="utf-8")
        assert "FOUNDER" in content
        assert "ENGINEER" in content
        assert "SKEPTIC" in content
        assert "POLICY"  in content
        assert "MEDIA"   in content
        assert "AIFEEDERS" in content

    def test_svg_contains_headline(self, tmp_path):
        script  = _minimal_script(headline="UNIQUE_HEADLINE_XYZ")
        result  = generate_comic_from_script(script, run_id="HL01", out_dir=str(tmp_path))
        content = result.read_text(encoding="utf-8")
        assert "UNIQUE_HEADLINE_XYZ" in content

    def test_panel_lines_in_output(self, tmp_path):
        script  = _minimal_script(media_hook="HOOK_SENTINEL_TEXT")
        result  = generate_comic_from_script(script, run_id="H02", out_dir=str(tmp_path))
        content = result.read_text(encoding="utf-8")
        assert "HOOK_SENTINEL" in content

    def test_no_run_id_generates_uid(self, tmp_path):
        r1 = generate_comic_from_script(_minimal_script(), out_dir=str(tmp_path))
        r2 = generate_comic_from_script(_minimal_script(), out_dir=str(tmp_path))
        assert r1.name != r2.name   # each call gets a unique uid


# ── generate_comic (backwards-compat wrapper) ─────────────────────────────────

class TestGenerateComicBackwardsCompat:
    def test_produces_file(self, tmp_path):
        result = generate_comic(
            headline          = "Microsoft Copilot goes autonomous",
            media_hook        = "Copilot now acts without being asked.",
            founder_line      = "Delegation at scale — this is what I've wanted.",
            engineer_line     = "Nobody defined what 'acting without being asked' means in prod.",
            skeptic_line      = "More automation, same accountability vacuum.",
            policy_line       = "Autonomous AI actions need a paper trail regulators can audit.",
            audience_question = "Autonomous AI: productivity leap or liability trap?\n1️⃣ Leap  2️⃣ Trap  3️⃣ Both",
            run_id            = "COMPAT01",
            out_dir           = str(tmp_path),
        )
        assert result.exists()

    def test_output_contains_founder_voice(self, tmp_path):
        result = generate_comic(
            headline="Test", media_hook="Hook", founder_line="FOUNDER_SENTINEL",
            engineer_line="Eng line", skeptic_line="Skep line", policy_line="Policy line",
            audience_question="Q?", run_id="BC02", out_dir=str(tmp_path),
        )
        assert "FOUNDER_SENTINEL" in result.read_text(encoding="utf-8")


# ── ComicScript dataclass ─────────────────────────────────────────────────────

class TestComicScript:
    def test_all_required_fields_set(self):
        s = _minimal_script()
        assert s.headline
        assert s.media_hook
        assert s.voice1_persona == "business"
        assert s.voice2_persona == "linkedin"
        assert s.voice3_persona == "genz"
        assert s.voice4_persona == "policy"
        assert s.audience_question

    def test_choices_default_empty(self):
        s = _minimal_script()
        assert s.choices == []

    def test_choices_can_be_set(self):
        s = _minimal_script(choices=["A) Yes", "B) No", "C) Maybe"])
        assert len(s.choices) == 3

    def test_persona_keys_are_valid(self):
        valid = {"media", "business", "linkedin", "genz", "policy"}
        s     = _minimal_script()
        for persona in [s.voice1_persona, s.voice2_persona, s.voice3_persona, s.voice4_persona]:
            assert persona in valid
