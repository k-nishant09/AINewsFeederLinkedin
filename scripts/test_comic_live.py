#!/usr/bin/env python3
"""
AIFeeders Comic Live Test
=========================
Builds a realistic comic script from hardcoded story data,
renders the PNG, and optionally publishes it to LinkedIn.

Usage
-----
# Render only (no LinkedIn)
    python3 scripts/test_comic_live.py

# Render + post to LinkedIn (requires running LinkedIn MCP + .env)
    python3 scripts/test_comic_live.py --post

# Override output directory
    python3 scripts/test_comic_live.py --out /tmp/my_comics

# Open the rendered SVG in the browser after generation
    python3 scripts/test_comic_live.py --open

Environment
-----------
Needs .env with LINKEDIN_MCP_URL (defaults: http://localhost:8104/mcp)
and LINKEDIN_ACCESS_TOKEN when --post is passed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import subprocess
from pathlib import Path

# Make sure the package is importable from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── Rich story fixtures — mirrors what the pipeline produces ──────────────────

# Each story is a self-contained dict that maps to the fields used by
# build_comic_script_from_summary() — but here we drive it directly
# via ComicScript so the test runs with zero LLM/MCP dependency.

STORIES = [
    {
        "id":    "STORY_COPILOT",
        "title": "Microsoft Copilot agents now run 24/7 without human prompts",
        "host_name":  "Maya",
        "host_role":  "Media Host",
        "news_brief": (
            "Microsoft just shipped a persistent Copilot agent — AI that works while you sleep, no prompt required. "
            "The idea: developers describe what they need, and the agent builds and hosts much of it automatically. "
            "But this raises a bigger question: does easier AI-driven development actually mean easier enterprise software?"
        ),
        "central_tension": "Can organisations govern software at the speed AI can now create it?",
        "cast": [
            {"name": "Arjun",  "role": "FutureAI Founder",     "persona": "business", "emoji": "💼"},
            {"name": "Steve",  "role": "Enterprise Engineer",   "persona": "linkedin", "emoji": "🧑‍💻"},
            {"name": "Nina",   "role": "Security Architect",    "persona": "genz",     "emoji": "🔐"},
            {"name": "Daniel", "role": "Technology CFO",        "persona": "policy",   "emoji": "💰"},
        ],
        "voice1_line": (
            "If my team can describe an application and have AI build and host much of it, "
            "we could test ideas in hours instead of weeks. For a startup, that could mean "
            "faster experiments with a much smaller engineering team."
        ),
        "voice2_line": (
            "That works beautifully in a demo. In our environment, the application still needs "
            "identity, API permissions, secrets, testing, monitoring and production approval. "
            "Can AI accelerate development without bypassing the controls that keep production safe?"
        ),
        "voice3_line": (
            "Steve's concern is exactly where I'd focus. Imagine an AI-generated application "
            "automatically requesting access to customer data or internal APIs. "
            "Can we prove what the agent accessed, why it accessed it, and who authorised it?"
        ),
        "voice4_line": (
            "If AI makes application creation dramatically cheaper, companies may create far more applications. "
            "But every application has an operating cost — infrastructure, security, monitoring and maintenance. "
            "What if AI reduces the cost of creating software but increases the cost of managing it?"
        ),
        "host_synthesis": (
            "Arjun sees faster experimentation. Steve sees integration complexity. "
            "Nina sees identity and security challenges. Daniel sees a potential governance and operating-cost problem. "
            "So perhaps the question isn't 'Can AI build software faster?' — it's 'Can organisations govern it at that speed?'"
        ),
        "audience_question": (
            "If AI dramatically reduces the cost of building applications, what becomes the biggest enterprise bottleneck?\n\n"
            "A — Development\n"
            "B — Security\n"
            "C — Governance\n"
            "D — Operations\n"
            "E — Something else\n\n"
            "Where do you stand? Drop your choice + your reasoning below 👇"
        ),
        "hashtags": ["#AIAgents", "#EnterpriseAI", "#SoftwareEngineering", "#MicrosoftAI", "#AI"],
    },
    {
        "id":    "STORY_DEEPSEEK",
        "title": "DeepSeek R2 beats GPT-4 at 10% of the compute cost",
        "host_name":  "Sophia",
        "host_role":  "Media Host",
        "news_brief": (
            "DeepSeek built a model that matches GPT-4 on benchmarks — at a tenth of the cost. "
            "This comes from a Chinese lab using open-weight architecture, challenging the assumption "
            "that frontier AI requires US-scale compute budgets. "
            "The question is: does cheaper mean enterprise-ready?"
        ),
        "central_tension": "When the cost of intelligence drops tenfold, what actually breaks — pricing power, trust, or governance?",
        "cast": [
            {"name": "Priya",   "role": "AI Infrastructure Founder", "persona": "business", "emoji": "💼"},
            {"name": "Anika",   "role": "ML Platform Engineer",       "persona": "linkedin", "emoji": "🧑‍💻"},
            {"name": "Jordan",  "role": "Generalist Thinker",         "persona": "genz",     "emoji": "⚖️"},
            {"name": "Rachel",  "role": "AI Policy Lead",             "persona": "policy",   "emoji": "🏛️"},
        ],
        "voice1_line": (
            "Our AI infrastructure bill just became a negotiating chip again. "
            "If a team can get GPT-4-level output at 10% of the cost, the ROI conversation changes — "
            "and so does the build-vs-buy decision for the next 18 months."
        ),
        "voice2_line": (
            "Priya, benchmark parity is not production parity. "
            "We need to validate latency, reliability, safety filters, and support contracts "
            "before recommending this to a production engineering team. We've been fooled before."
        ),
        "voice3_line": (
            "Anika's validation concern is valid, but the speed of adoption will outpace the validation. "
            "Cheaper models don't automatically solve the safety problem — they make it cheaper to ignore. "
            "Who is responsible when an enterprise deploys this and something goes wrong?"
        ),
        "voice4_line": (
            "A Chinese open-weight model deployed in EU data centres triggers GDPR and data sovereignty "
            "questions that most enterprise legal teams haven't answered. "
            "The price drop is real. The regulatory clarity is not."
        ),
        "host_synthesis": (
            "Priya sees a cost advantage. Anika sees a validation gap. "
            "Jordan sees accountability being bypassed by adoption speed. Rachel sees a regulatory blind spot. "
            "The real question is not whether DeepSeek R2 is cheaper — it's whether organisations are ready to govern what they deploy."
        ),
        "audience_question": (
            "DeepSeek R2 at 10% of GPT-4 cost. What breaks first?\n\n"
            "A — Pricing power of US labs\n"
            "B — Enterprise trust in open-weight models\n"
            "C — GPU investment thesis\n"
            "D — Regulatory frameworks\n"
            "E — Nothing — this has happened before\n\n"
            "Pick ONE. Defend it against the strongest counter 👇"
        ),
        "hashtags": ["#DeepSeek", "#OpenWeightAI", "#EnterpriseAI", "#AIGovernance", "#GenerativeAI"],
    },
    {
        "id":    "STORY_EUAIACT",
        "title": "EU AI Act enforcement begins — high-risk AI systems face audits",
        "host_name":  "Marcus",
        "host_role":  "Media Host",
        "news_brief": (
            "The EU AI Act just moved from theory to enforcement. "
            "High-risk AI systems — those used in hiring, credit, law enforcement and critical infrastructure — "
            "now have 90 days to demonstrate compliance or face significant penalties. "
            "The question for every team shipping AI into Europe: are you actually ready?"
        ),
        "central_tension": "Can engineering teams document and audit AI systems at the pace regulation now demands?",
        "cast": [
            {"name": "Leo",     "role": "AI Startup Founder",  "persona": "business", "emoji": "💼"},
            {"name": "Tom",     "role": "Enterprise Architect", "persona": "linkedin", "emoji": "🧑‍💻"},
            {"name": "Zoe",     "role": "Generalist Thinker",   "persona": "genz",     "emoji": "⚖️"},
            {"name": "Michael", "role": "Compliance Lead",      "persona": "policy",   "emoji": "🏛️"},
        ],
        "voice1_line": (
            "Every AI product we ship to Europe now needs a conformity assessment. "
            "For a startup, that means legal costs that rival engineering costs. "
            "We are seriously reconsidering our EU go-to-market timeline."
        ),
        "voice2_line": (
            "Leo, the conformity assessment is actually the least of the problems. "
            "Good luck getting explainability documentation from a 70B parameter model — "
            "the tooling to produce that documentation at production scale doesn't exist yet."
        ),
        "voice3_line": (
            "Tom, the tooling will catch up — but the timeline matters. "
            "Regulation written by people who've never deployed an LLM is still regulation you have to follow. "
            "The 90-day window was set without asking how long explainability tooling actually takes to build."
        ),
        "voice4_line": (
            "The 90-day window is the same window that caught GDPR teams off guard in 2018. "
            "The organisations that are scrambling now are the same ones that said 'we'll figure it out later' "
            "when the Act was first published. The history is repeating."
        ),
        "host_synthesis": (
            "Leo sees startup cost pressure. Tom sees tooling gaps. "
            "Zoe sees a timeline mismatch between policy and engineering reality. "
            "Michael sees a pattern of regulatory unpreparedness repeating. "
            "The common thread: the 90-day clock is running, and most teams are not ready."
        ),
        "audience_question": (
            "EU AI Act enforcement is live. What's your team actually doing?\n\n"
            "1️⃣ We're already compliant\n"
            "2️⃣ We're scrambling to catch up\n"
            "3️⃣ We're technically out of scope — for now\n"
            "4️⃣ We're geofencing Europe until this is clearer\n\n"
            "Be honest — and if you're in a different situation, tell us below 👇"
        ),
        "hashtags": ["#EUAIAct", "#AIRegulation", "#AIGovernance", "#EnterpriseAI", "#AICompliance"],
    },
]


def build_script_from_story(story: dict):
    """Build a ComicScript from a story fixture dict."""
    from daily_news.agents.comic_generator import ComicScript, PersonaCast
    cast = [
        PersonaCast(
            name    = c["name"],
            role    = c["role"],
            persona = c["persona"],
            emoji   = c["emoji"],
        )
        for c in story["cast"]
    ]
    return ComicScript(
        headline          = story["title"],
        host_name         = story["host_name"],
        host_role         = story["host_role"],
        news_brief        = story["news_brief"],
        central_tension   = story.get("central_tension", ""),
        cast              = cast,
        voice1_line       = story["voice1_line"],
        voice2_line       = story["voice2_line"],
        voice3_line       = story["voice3_line"],
        voice4_line       = story["voice4_line"],
        host_synthesis    = story["host_synthesis"],
        audience_question = story["audience_question"],
        hashtags          = story.get("hashtags", []),
    )


# ── Render ─────────────────────────────────────────────────────────────────────

def render_all(out_dir: str) -> list[tuple[dict, Path]]:
    """Render all story fixtures and return (story, path) pairs."""
    from daily_news.agents.comic_generator import generate_comic_from_script

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    results = []
    for story in STORIES:
        script = build_script_from_story(story)
        path   = generate_comic_from_script(script, run_id=story["id"], out_dir=out_dir)
        suffix = path.suffix.upper()
        size   = path.stat().st_size
        print(f"  ✅ {story['id']} → {path.name}  [{suffix}, {size:,} bytes]")
        results.append((story, path))
    return results


# ── LinkedIn post ─────────────────────────────────────────────────────────────

async def post_to_linkedin(story: dict, comic_path: Path) -> dict:
    """Upload comic image and create LinkedIn post with the first story."""
    # Load env
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    from daily_news.mcp.linkedin import LinkedInMCPClient

    client = LinkedInMCPClient()

    # 1. Validate token first
    print("\n  🔑 Validating LinkedIn token…")
    try:
        profile = await client.get_profile()
        name    = profile.get("name") or profile.get("localizedFirstName", "?")
        print(f"     ✅ Connected as: {name}")
    except Exception as e:
        print(f"     ❌ Token validation failed: {e}")
        print("        Make sure LinkedIn MCP is running: docker compose up linkedin-mcp")
        return {}

    # 2. Upload image
    print(f"\n  📤 Uploading comic image: {comic_path.name}…")
    upload = await client.upload_image(
        image_path  = str(comic_path),
        description = f"AIFeeders comic: {story['title'][:80]}",
    )
    asset_urn    = upload.get("asset_urn", "")
    upload_status = upload.get("status", "?")
    if asset_urn:
        print(f"     ✅ Uploaded → asset_urn: {asset_urn}")
    else:
        print(f"     ⚠️  Upload returned no asset_urn (status={upload_status})")
        print("        Will fall back to text-only post.")

    # 3. Build post text — outside-image CTA block
    script    = build_script_from_story(story)
    post_text = _build_post_text(story, script)
    print(f"\n  📝 Post text ({len(post_text)} chars):")
    print("  " + "\n  ".join(post_text[:400].split("\n")))
    print("  …")

    # 4. Publish
    print("\n  🚀 Publishing to LinkedIn…")
    from datetime import date
    import hashlib
    pub_key = f"comic-test:{date.today().isoformat()}:{hashlib.sha256(post_text.encode()).hexdigest()[:8]}"

    if asset_urn:
        result = await client.create_post_with_image(
            text            = post_text,
            asset_urn       = asset_urn,
            publication_key = pub_key,
        )
    else:
        result = await client.create_post(
            text            = post_text,
            publication_key = pub_key,
        )

    return result


def _build_post_text(story: dict, script) -> str:
    """Build the outside-image LinkedIn post caption using build_outside_post."""
    from daily_news.agents.comic_generator import build_outside_post
    return build_outside_post(script)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_svg(path: Path) -> list[str]:
    """Return a list of validation issues (empty = all good).

    When the output is a PNG (binary), validation is skipped — the file
    is assumed correct if it exists and has a non-zero size.
    """
    issues = []

    # If cairosvg or rsvg produced a real PNG, skip text-based checks
    if path.suffix.lower() == ".png":
        if not path.exists() or path.stat().st_size == 0:
            issues.append("PNG file missing or empty")
        return issues

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"Cannot read file: {e}"]

    # Must be SVG
    if "<svg" not in text:
        issues.append("No <svg> root element")
        return issues

    # Dimensions
    if 'width="1440"' not in text:
        issues.append("Missing expected width=1440")
    if 'height="896"' not in text:
        issues.append("Missing expected height=896")

    # Brand text
    if "AIFeeders" not in text:
        issues.append("Missing AIFeeders brand text")
    if "ONE NEWS" not in text:
        issues.append("Missing header tagline")
    if "TODAY'S VOICES" not in text:
        issues.append("Missing TODAY'S VOICES roster in Scene 1")

    # 6 accent stripes (height="5" in stripe rects)
    stripe_count = text.count('height="5"')
    if stripe_count != 6:
        issues.append(f"Expected 6 accent stripes, found {stripe_count}")

    # Character ellipses: scene1 has 1 host char; scenes 2-6 have listener+speaker = 10 more ≥11
    ellipse_count = text.count("<ellipse")
    if ellipse_count < 11:
        issues.append(f"Expected ≥11 character body ellipses, found {ellipse_count}")

    # Polygons: 6 bubble tails + 5 reply-arrow arrowheads = ≥11
    polygon_count = text.count("<polygon")
    if polygon_count < 11:
        issues.append(f"Expected ≥11 polygons (bubble tails + arrowheads), found {polygon_count}")

    return issues


def run_validation(results: list[tuple[dict, Path]]) -> bool:
    """Validate all generated files. Returns True if all pass."""
    print("\n── Validation ───────────────────────────────────────")
    all_ok = True
    for story, path in results:
        issues = validate_svg(path)
        if issues:
            print(f"  ❌ {story['id']}: {len(issues)} issue(s):")
            for iss in issues:
                print(f"      • {iss}")
            all_ok = False
        else:
            print(f"  ✅ {story['id']}: all checks passed")
    return all_ok


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="AIFeeders Comic Live Test")
    parser.add_argument("--post",   action="store_true", help="Post first story to LinkedIn")
    parser.add_argument("--open",   action="store_true", help="Open rendered files in browser")
    parser.add_argument("--story",  default=None,        help="Story ID to post (default: first story)")
    parser.add_argument("--out",    default="/tmp/aifeeders_comics", help="Output directory")
    args = parser.parse_args()

    print("═" * 58)
    print("  🧠 AIFeeders Comic Generator — Live Test")
    print("═" * 58)

    # ── Step 1: Render ─────────────────────────────────────────────────────────
    print(f"\n── Rendering {len(STORIES)} comics → {args.out}")
    results = render_all(args.out)

    # ── Step 2: Validate ───────────────────────────────────────────────────────
    ok = run_validation(results)

    # ── Step 3: Open (optional) ────────────────────────────────────────────────
    if args.open:
        for _, path in results:
            print(f"\n  🔍 Opening {path.name}…")
            try:
                subprocess.run(["open", str(path)], check=False)
            except Exception:
                print(f"      (run: open {path})")

    # ── Step 4: Post (optional) ────────────────────────────────────────────────
    if args.post:
        # Pick which story to post
        story_id = args.story or STORIES[0]["id"]
        target   = next((s for s in STORIES if s["id"] == story_id), STORIES[0])
        target_path = next(p for st, p in results if st["id"] == target["id"])

        print(f"\n── LinkedIn publish: {target['id']}")
        result = await post_to_linkedin(target, target_path)

        if result:
            status = result.get("status", "?")
            urn    = result.get("post_urn", "?")
            img    = result.get("image_attached", False)
            print(f"\n  🎉 Result: status={status}  urn={urn}  image_attached={img}")
            if status == "error":
                print(f"     Error: {result.get('li_message') or result.get('error', '?')}")
        else:
            print("\n  ⚠️  No result returned — check MCP server logs.")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "═" * 58)
    if ok:
        print("  ✅  All comics rendered and validated successfully.")
    else:
        print("  ❌  Some validation checks failed — see above.")
    print("═" * 58)

    for _, path in results:
        print(f"  📄 {path}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
