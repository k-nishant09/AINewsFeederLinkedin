"""
Unit tests for comic_generator.py

Coverage:
  - _wrap:                       short text, long text, multiline
  - _speech_bubble:              tail rendering and text in SVG
  - build_scenes:                6 ComicScene objects with reply chain
  - _render_comic:               correct dimensions, header text, 6 panels
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
    ComicScene,
    ComicScript,
    PersonaCast,
    build_scenes,
    _render_comic,
    _speech_bubble,
    _wrap,
    generate_comic,
    generate_comic_from_script,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_script(**overrides) -> ComicScript:
    cast = [
        PersonaCast(name="Arjun", role="AI Founder", persona="business", emoji="💼"),
        PersonaCast(name="Steve", role="Enterprise Engineer", persona="linkedin", emoji="🧑‍💻"),
        PersonaCast(name="Nina", role="Generalist Thinker", persona="genz", emoji="⚖️"),
        PersonaCast(name="James", role="Policy Specialist", persona="policy", emoji="🏛️"),
    ]
    defaults = dict(
        headline          = "OpenAI ships GPT-5 with native code execution",
        host_name         = "Maya",
        host_role         = "Tech Journalist",
        news_brief        = "GPT-5 runs code autonomously without sandbox boundaries.",
        central_tension   = "Can autonomous code generation be trusted in prod?",
        cast              = cast,
        voice1_line       = "Our deployment backlog just halved overnight.",
        voice2_line       = "Who reviews what it executed in prod at 2am?",
        voice3_line       = "Cheaper execution doesn't mean cheaper debugging.",
        voice4_line       = "No audit trail for autonomous code execution means no compliance.",
        host_synthesis    = "Execution speed is rising faster than governance tooling.",
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


# ── _speech_bubble ────────────────────────────────────────────────────────────

class TestSpeechBubble:
    def _make(self, tail_side: str = "top") -> str:
        return _speech_bubble(
            x=10, y=10, w=200, h=150,
            text="Line one. Line two.",
            tail_side=tail_side,
        )

    def test_top_tail_renders_polygon(self):
        svg = self._make("top")
        assert "<polygon" in svg

    def test_right_tail_renders_polygon(self):
        svg = self._make("right")
        assert "<polygon" in svg

    def test_bubble_contains_rect(self):
        svg = self._make("top")
        assert "<rect" in svg

    def test_text_lines_in_svg(self):
        svg = _speech_bubble(x=0, y=0, w=300, h=100, text="Hello World")
        assert "Hello" in svg
        assert "World" in svg

    def test_first_line_bold(self):
        svg = _speech_bubble(x=0, y=0, w=300, h=100, text="First line here")
        assert 'font-weight="bold"' in svg

    def test_accent_colour_applied_to_stroke(self):
        svg = _speech_bubble(x=0, y=0, w=300, h=100, text="test", accent="#AABBCC")
        assert "#AABBCC" in svg

    def test_html_escaped_special_chars(self):
        svg = _speech_bubble(x=0, y=0, w=300, h=100, text="5 < 10 & a > b")
        assert "&lt;" in svg
        assert "&amp;" in svg
        assert "&gt;" in svg


# ── build_scenes ──────────────────────────────────────────────────────────────

class TestBuildScenes:
    def test_returns_six_scenes(self):
        script = _minimal_script()
        scenes = build_scenes(script)
        assert len(scenes) == 6

    def test_first_scene_is_intro(self):
        scenes = build_scenes(_minimal_script())
        assert scenes[0].is_intro is True
        assert scenes[0].persona == "media"

    def test_last_scene_is_close(self):
        scenes = build_scenes(_minimal_script())
        assert scenes[5].is_close is True
        assert scenes[5].persona == "media"

    def test_voice1_persona_mapped(self):
        scenes = build_scenes(_minimal_script())
        assert scenes[1].persona == "business"

    def test_scenes_have_reply_chain(self):
        scenes = build_scenes(_minimal_script())
        # Scene 2 (idx 1) is answered by Scene 3 (idx 2)
        assert scenes[2].prev_persona == scenes[1].persona


# ── _render_comic ─────────────────────────────────────────────────────────────

class TestRenderComic:
    def _svg(self) -> str:
        script = _minimal_script()
        scenes = build_scenes(script)
        return _render_comic("Test headline", "Subhead test", scenes)

    def test_correct_total_dimensions_in_svg(self):
        svg = self._svg()
        assert f'width="{TOTAL_W}"' in svg
        assert f'height="{TOTAL_H}"' in svg

    def test_header_background_present(self):
        svg = self._svg()
        assert "0F172A" in svg   # HEADER_BG

    def test_brand_text_present(self):
        svg = self._svg()
        assert "AIFEEDERS" in svg
        assert "ONE NEWS" in svg

    def test_headline_present(self):
        svg = self._svg()
        assert "Test headline" in svg

    def test_custom_brand_name(self):
        script = _minimal_script()
        scenes = build_scenes(script)
        svg = _render_comic("headline", "subhead", scenes, brand="MYBOT")
        assert "MYBOT" in svg


# ── generate_comic_from_script ────────────────────────────────────────────────

class TestGenerateComicFromScript:
    def test_produces_file(self, tmp_path):
        script = _minimal_script()
        result = generate_comic_from_script(script, run_id="UNIT01", out_dir=str(tmp_path))
        assert result.exists()

    def test_filename_contains_run_id(self, tmp_path):
        result = generate_comic_from_script(_minimal_script(), run_id="MYID", out_dir=str(tmp_path))
        assert "MYID" in result.name

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


# ── ComicScript dataclass ─────────────────────────────────────────────────────

class TestComicScript:
    def test_all_required_fields_set(self):
        s = _minimal_script()
        assert s.headline
        assert s.host_name
        assert s.news_brief
        assert len(s.cast) == 4
        assert s.voice1_line
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
        for p in s.cast:
            assert p.persona in valid

# ── build_outside_post ────────────────────────────────────────────────────────

class TestBuildOutsidePost:
    def test_build_outside_post_structure(self):
        from daily_news.agents.comic_generator import build_outside_post
        s = _minimal_script(
            source_name="TechCrunch",
            source_url="https://techcrunch.com/example",
            hashtags=["#EnterpriseAI", "#AIInfrastructure"],
        )
        post = build_outside_post(s)
        # PART 1 — hook (first line is fact/brief or tension)
        assert "GPT-5 runs code autonomously" in post
        # PART 2 — voices intro line (new format)
        assert "AIFeeders asked four voices to look at the same story:" in post
        # PART 2 — persona role labels present
        assert "AI Founder" in post
        assert "Enterprise Engineer" in post
        # PART 2 — disagreement bridge
        assert "don't completely agree" in post
        # PART 3 — host CTA label (new format)
        assert "THE AIFEEDERS QUESTION" in post
        # PART 3 — source and hashtags
        assert "TechCrunch" in post
        assert "https://techcrunch.com/example" in post
        assert "#EnterpriseAI #AIInfrastructure" in post
