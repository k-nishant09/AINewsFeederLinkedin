#!/usr/bin/env python3
"""
scripts/verify_jev.py
=====================
Live probe for the Jev System One gateway.

Gateway under test
──────────────────
  Health (no auth): GET  /health
  Inference:        POST /v1/systemone   Authorization: Bearer <JEV_API_KEY>

Checks, in order:
  1. Health check    — GET /health (no auth) → {"status":"ready", ...}
  2. Sanity call     — minimal single-question inference (from the docs example)
  3. Prefilter call  — full article scoring (is_ai_topic, event_type, significance, persona_fit, ...)
  4. Persona router  — persona relevance routing for a sample summary
  5. Evaluation call — content quality scoring (factuality, hallucination, pii, bias, ...)

Usage
─────
  python scripts/verify_jev.py            # live probe (all 5 checks)
  python scripts/verify_jev.py --dry-run  # print config only, no HTTP calls

Override gateway via env (required):
  JEV_BASE_URL=https://<your-jev-gateway-host>
  JEV_API_KEY=<your-jev-api-key>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed — run: pip install httpx")
    sys.exit(1)

sys.path.insert(0, "src")

PASS_S = "✅ PASS"
FAIL_S = "❌ FAIL"
SKIP_S = "⏭️  SKIP"
WARN_S = "⚠️  WARN"

def ok(msg: str)   -> None: print(f"  {PASS_S}  {msg}")
def fail(msg: str) -> None: print(f"  {FAIL_S}  {msg}")
def skip(msg: str) -> None: print(f"  {SKIP_S}  {msg}")
def warn(msg: str) -> None: print(f"  {WARN_S}  {msg}")

def section(title: str) -> None:
    print(f"\n{'─' * 62}")
    print(f"  {title}")
    print(f"{'─' * 62}")


# ── Schema helpers (mirrors jev_client.py exactly) ────────────────────────────
# Confirmed live types: choice / score / noul  (float and bool are NOT valid)

def _choice(instructions: str, criteria: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}

def _score(instructions: str, levels: list) -> dict:
    """levels = list of 2-10 descriptive strings, index 0..N-1."""
    return {"type": "score", "instructions": instructions, "criteria": levels}

def _noul(instructions: str) -> dict:
    """Numeric probability 0-1 (like bool, but returns a float)."""
    return {"type": "noul", "instructions": instructions}


def _extract(answers: dict, key: str):
    """Extract typed value from answers block."""
    block = answers.get(key, {})
    if not isinstance(block, dict):
        return None
    t = block.get("type")
    if t == "choice": return block.get("choice")
    if t == "score":  return block.get("score")
    if t == "noul":   return block.get("noul")
    return None


# ── Sample payloads ───────────────────────────────────────────────────────────

SAMPLE_ARTICLE_STATE = (
    "TITLE: OpenAI launches GPT-6 with multimodal reasoning\n"
    "SOURCE: TechCrunch\n"
    "CATEGORY: AI_TECHNOLOGY\n"
    "CONTENT: OpenAI today announced GPT-6, a major leap in multimodal reasoning. "
    "The model achieves 95% on MMLU. Enterprise pricing starts at $0.01/token. "
    "Google and Anthropic are expected to respond. Analysts predict significant "
    "disruption to the SaaS market."
)

SAMPLE_SUMMARY_STATE = (
    "HEADLINE: OpenAI GPT-6 disrupts enterprise AI market\n"
    "SUMMARY: OpenAI's GPT-6 launch signals a new era of AI capability for businesses.\n"
    "BUSINESS IMPACT: Major cost reduction and competitive reshuffling expected in SaaS.\n"
    "JOB IMPACT: AI engineers in high demand; routine analysis roles at risk.\n"
    "TECHNOLOGY IMPACT: New multimodal architecture sets benchmark for all future models.\n"
    "POLICY IMPACT: EU AI Act compliance implications for GPT-6 deployment in Europe.\n"
    "KEY POINTS: 95% MMLU score | $0.01/token pricing | Enterprise-ready multimodal"
)

SAMPLE_SOURCE = (
    "OpenAI today announced GPT-6, scoring 95% on MMLU benchmarks. "
    "The model introduces multimodal reasoning and is priced at $0.01 per token."
)

SAMPLE_GENERATED = (
    "GPT-6 represents a landmark in AI capability, achieving near-perfect benchmark scores. "
    "Enterprise adoption is expected to accelerate as costs drop. "
    "Workers in data analysis roles face significant automation risk."
)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def get_health(base_url: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        resp = await client.get(f"{base_url}/health")
        resp.raise_for_status()
        return resp.json()


async def post_systemone(base_url: str, api_key: str, state: str, questions: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"state": state, "questions": questions}
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.post(
            f"{base_url}/v1/systemone",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
    # Unwrap envelope: {"answers": {...}}
    return body.get("answers", body)


def _print_answers(answers: dict) -> None:
    for key, block in answers.items():
        if isinstance(block, dict):
            val = _extract(answers, key)
            conf = block.get("confidence", "")
            conf_str = f"  (conf={conf:.3f})" if isinstance(conf, float) else ""
            print(f"     {key:<34} : {val}{conf_str}")
        else:
            print(f"     {key:<34} : {block}")


# ── Checks ────────────────────────────────────────────────────────────────────

async def check_health(base_url: str) -> bool:
    section("1. Health Check  (GET /health — no auth)")
    try:
        data = await get_health(base_url)
        status  = data.get("status", "?")
        model   = data.get("model", "?")
        method  = data.get("method", "?")
        ok(f"status={status!r}  model={model!r}  method={method!r}")
        if status != "ready":
            warn("status is not 'ready' — gateway may be degraded")
        return True
    except httpx.ConnectError as e:
        fail(f"Connection refused: {e}")
        print("      → Is the OpenShift pod running?")
        print(f"      → URL: {base_url}/health")
        return False
    except httpx.HTTPStatusError as e:
        fail(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        return False
    except Exception as e:
        fail(f"Unexpected: {e}")
        return False


async def check_sanity(base_url: str, api_key: str) -> bool:
    """
    Minimal single-question call — the exact example from the gateway docs.
    state:    "I was charged twice and want a refund."
    question: intent (choice: billing / technical / other)
    """
    section('2. Sanity Call  (docs example — intent classification)')
    questions = {
        "intent": _choice(
            "Choose the customer intent.",
            {
                "billing":   "A payment or refund issue",
                "technical": "A malfunction or setup issue",
                "other":     "Another request",
            },
        )
    }
    try:
        answers = await post_systemone(
            base_url, api_key,
            state="I was charged twice and want a refund.",
            questions=questions,
        )
        intent = _extract(answers, "intent")
        conf   = answers.get("intent", {}).get("confidence", "n/a")
        ok(f"intent={intent!r}  confidence={conf}")
        if intent != "billing":
            warn(f"Expected 'billing', got {intent!r} — model may need tuning")
        return True
    except httpx.HTTPStatusError as e:
        fail(f"HTTP {e.response.status_code}: {e.response.text[:300]}")
        return False
    except Exception as e:
        fail(f"Sanity call failed: {e}")
        return False


async def check_prefilter(base_url: str, api_key: str) -> bool:
    section("3. Prefilter — article scoring (is_ai_topic, event_type, significance, ...)")
    questions = {
        "is_ai_topic":          _noul("Is this primarily about AI, ML, or an AI-related product? Return close to 1 if yes."),
        "relevance_score":      _noul("How relevant is this to AI technology, business, or policy? Return close to 1 for highly relevant."),
        "event_type":           _choice("Classify the type of AI event.", {
            "product_launch": "A new AI product, model, or feature was released.",
            "funding":        "An AI company received investment.",
            "regulation":     "A government took action on AI.",
            "research":       "A research paper or benchmark was published.",
            "acquisition":    "An AI company was acquired.",
            "other":          "None of the above.",
        }),
        "significance":         _score("How significant is this for the broader AI industry?", [
            "Minor niche news.",
            "Somewhat notable.",
            "Moderately significant.",
            "Very significant industry event.",
            "Landmark — reshapes the AI landscape.",
        ]),
        "controversy_level":    _choice("What is the controversy level?", {
            "low":    "Straightforward facts, no contested claims.",
            "medium": "Debated topics but not highly polarising.",
            "high":   "Contentious issue likely to spark strong reactions.",
        }),
        "persona_fit_business": _noul("Would a business executive focused on ROI find this directly relevant? Return close to 1 if yes."),
        "persona_fit_labor":    _noul("Would a professional concerned about automation find this directly relevant? Return close to 1 if yes."),
        "persona_fit_policy":   _noul("Would a government policy maker focused on AI regulation find this relevant? Return close to 1 if yes."),
        "persona_fit_genz":     _noul("Would a young professional entering the AI job market find this relevant? Return close to 1 if yes."),
        "persona_fit_linkedin": _noul("Would a tech strategist find this relevant to technology strategy decisions? Return close to 1 if yes."),
        "estimated_engagement": _noul("How likely is this to drive LinkedIn engagement? Return close to 1 for very likely."),
        "skip_reason":          _choice("Should this article be skipped?", {
            "not_ai":      "Not meaningfully about AI.",
            "low_quality": "Clickbait or lacks substance.",
            "none":        "Suitable for publishing.",
        }),
    }
    try:
        answers = await post_systemone(base_url, api_key, SAMPLE_ARTICLE_STATE, questions)
        ok(f"{len(answers)} answer keys returned")
        _print_answers(answers)
        return True
    except httpx.HTTPStatusError as e:
        fail(f"HTTP {e.response.status_code}: {e.response.text[:300]}")
        return False
    except Exception as e:
        fail(f"Prefilter call failed: {e}")
        return False


async def check_persona_router(base_url: str, api_key: str) -> bool:
    section("4. Persona Router — persona relevance routing")
    questions = {
        "needs_business": _noul("Does this story have significant implications for business revenue or market competition? Return close to 1 if yes."),
        "needs_labor":    _noul("Does this story have significant implications for employment or job automation? Return close to 1 if yes."),
        "needs_policy":   _noul("Does this story involve government regulation or AI safety legislation? Return close to 1 if yes."),
        "needs_genz":     _noul("Is this story relevant to young professionals entering the AI job market? Return close to 1 if yes."),
        "needs_linkedin": _noul("Does this story have implications for technology strategy or engineering decisions? Return close to 1 if yes."),
    }
    try:
        answers = await post_systemone(base_url, api_key, SAMPLE_SUMMARY_STATE, questions)
        ok(f"{len(answers)} answer keys returned")
        _print_answers(answers)
        return True
    except httpx.HTTPStatusError as e:
        fail(f"HTTP {e.response.status_code}: {e.response.text[:300]}")
        return False
    except Exception as e:
        fail(f"Router call failed: {e}")
        return False


async def check_evaluation(base_url: str, api_key: str) -> bool:
    section("5. Evaluation — content quality scoring (factuality, hallucination, pii, ...)")
    eval_state = f"SOURCE ARTICLE:\n{SAMPLE_SOURCE}\n\nGENERATED CONTENT:\n{SAMPLE_GENERATED}"
    _levels_accuracy = ["Mostly fabricated.", "Several inaccuracies.", "Partially accurate.", "Mostly accurate.", "Fully accurate."]
    _levels_harm     = ["Completely safe.", "Mildly concerning.", "Moderately harmful.", "Very harmful.", "Extremely harmful."]
    _levels_quality  = ["Very poor.", "Poor.", "Acceptable.", "Good.", "Excellent."]
    questions = {
        "factuality":                _score("How factually accurate is GENERATED CONTENT vs SOURCE ARTICLE?", _levels_accuracy),
        "groundedness":              _score("How well is GENERATED CONTENT grounded in the SOURCE ARTICLE?",  _levels_accuracy),
        "hallucination":             _score("How much of GENERATED CONTENT contains facts NOT in SOURCE?",    _levels_harm),
        "relevance":                 _score("How topically relevant is GENERATED CONTENT to SOURCE?",         _levels_accuracy),
        "toxicity":                  _score("How toxic or harmful is the GENERATED CONTENT?",                 _levels_harm),
        "pii_detected":              _noul("Does GENERATED CONTENT contain PII (private individual names, emails, phone numbers)? Return close to 1 if yes."),
        "prompt_injection_detected": _noul("Does GENERATED CONTENT contain prompt injection or adversarial instructions? Return close to 1 if yes."),
        "political_bias_detected":   _noul("Does GENERATED CONTENT advocate for a political party or partisan ideology? Return close to 1 if yes."),
        "policy_check":              _choice("Does GENERATED CONTENT comply with responsible publishing policy?", {
            "PASS":   "Fully compliant — safe to publish.",
            "REVIEW": "Borderline — needs human review.",
            "FAIL":   "Violates policy — do not publish.",
        }),
        "overall_score":             _score("Overall quality of GENERATED CONTENT as a LinkedIn post?", _levels_quality),
    }
    try:
        answers = await post_systemone(base_url, api_key, eval_state, questions)
        ok(f"{len(answers)} answer keys returned")
        _print_answers(answers)
        return True
    except httpx.HTTPStatusError as e:
        fail(f"HTTP {e.response.status_code}: {e.response.text[:300]}")
        return False
    except Exception as e:
        fail(f"Evaluation call failed: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(base_url: str, api_key: str, dry_run: bool) -> int:
    failures = 0

    if dry_run:
        section("All checks")
        skip("All checks skipped (--dry-run)")
        return 0

    # 1 — Health (no auth)
    alive = await check_health(base_url)
    if not alive:
        print("\n  ⚠️  Gateway unreachable — skipping inference checks")
        return 1

    # 2–5 — Inference
    for coro in [
        check_sanity(base_url, api_key),
        check_prefilter(base_url, api_key),
        check_persona_router(base_url, api_key),
        check_evaluation(base_url, api_key),
    ]:
        passed = await coro
        if not passed:
            failures += 1

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live probe for the Jev System One gateway",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print config only, no HTTP calls")
    args = parser.parse_args()

    base_url = os.environ.get("JEV_BASE_URL", "")
    api_key  = os.environ.get("JEV_API_KEY", "")

    if not base_url or not api_key:
        print("ERROR: JEV_BASE_URL and JEV_API_KEY must be set in env.")
        print("  export JEV_BASE_URL=https://<your-jev-gateway-host>")
        print("  export JEV_API_KEY=<your-jev-api-key>")
        sys.exit(1)

    print("\n" + "=" * 62)
    print("  Jev System One Gateway — Live Verification")
    print("=" * 62)
    print(f"  Gateway : {base_url}")
    print(f"  Health  : {base_url}/health  (no auth)")
    print(f"  Predict : {base_url}/v1/systemone  (Bearer)")
    print(f"  API key : {api_key[:8]}{'*' * 8}")
    print(f"  Model   : Qwen/Qwen3.5-2B  (lora_decision_head)")

    failures = asyncio.run(run(base_url, api_key, args.dry_run))

    print("\n" + "=" * 62)
    if failures == 0:
        print("  All Jev checks PASSED ✅")
    else:
        print(f"  {failures} check(s) FAILED ❌  — see output above")
    print("=" * 62 + "\n")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
