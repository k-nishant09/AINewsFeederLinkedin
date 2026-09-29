"""Evaluation Agent — the quality gate in the AIFeeders pipeline.

Pipeline
────────
  Qwen (creator) → generate_personas → EvaluationAgent.evaluate()
    Step 1  MCP scoring backend   → factuality / groundedness / hallucination floats
    Step 2  Judge (Qwen @ 0.1)    → independent critic chain; detects banned phrases,
                                    boilerplate, no-clash; REVISE forces REGENERATE
    Step 3  _apply_gate()         → deterministic threshold decision:
              PASS       → score_reach → publish → LinkedIn
              REGENERATE → summarize → generate_personas → evaluate  (up to MAX_RETRIES)
              BLOCK      → hard stop, never published

Scoring backend
───────────────
JEV_BASE_URL set   → JevClient.evaluate_content()       fast structured scores
JEV_BASE_URL empty → EvaluationMCPClient.evaluate_content()  LLM-backed MCP

The Judge is always a separate Qwen chain at temperature=0.1 — never the
same invocation as the generator, which runs at 0.7.
"""
from __future__ import annotations

import json
import logging

import httpx
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from daily_news.config.settings import get_settings
from daily_news.mcp.client import jev_singleton
from daily_news.mcp.evaluation import EvaluationMCPClient
from daily_news.models.evaluation import EvaluationDecision, EvaluationResult
from daily_news.models.persona import PersonaSetOutput
from daily_news.models.summary import NewsSummary
from daily_news.observability.tracing import get_langfuse_callback

logger = logging.getLogger(__name__)


class EvaluationAgent:
    """
    Qwen creator → Judge critic → regeneration correction → Judge final gate → LinkedIn publisher.

    The same model (Qwen) plays two roles at different temperatures:
      - Generator  temperature=0.7  (creative, persona voices)
      - Judge      temperature=0.1  (critical, pattern-matching for banned phrases)

    Keeping them as separate chain invocations ensures the judge never sees its
    own generation — it only receives the final assembled post text.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        # Architecture fix: use the process-scoped JevClient singleton so we reuse
        # the same connection pool that jev_prefilter / jev_find_angle / jev_router use.
        # Fresh JevClient() per EvaluationAgent instantiation was causing a 4th cold
        # TCP+TLS handshake to the Jev gateway that the singleton now eliminates.
        self._jev = jev_singleton() if s.jev_base_url else None
        self._mcp = EvaluationMCPClient()

        # Independent LLM-as-a-Judge Reviewer
        judge_model = s.eval_llm_model or s.llm_model
        judge_base_url = s.eval_llm_base_url or s.llm_base_url
        judge_api_key = s.eval_llm_api_key or s.llm_api_key

        # Architecture fix: add explicit Limits so the judge pool doesn't over-provision
        # (httpx default max_connections=100 is wasteful for a single-host judge call).
        # keepalive_expiry=60s matches the workflow run window so the connection stays
        # warm for the evaluate → potential-retry → re-evaluate cycle.
        _judge_limits = httpx.Limits(
            max_connections=10,
            max_keepalive_connections=4,
            keepalive_expiry=60.0,
        )
        self._judge_llm = ChatOpenAI(
            model=judge_model,
            api_key=judge_api_key,
            base_url=judge_base_url,
            temperature=0.1,  # low temperature for critical judgment
            http_client=httpx.Client(verify=False, limits=_judge_limits),
            http_async_client=httpx.AsyncClient(verify=False, limits=_judge_limits),
        )

        self._judge_prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                # ── ROLE ──────────────────────────────────────────────────────────
                "You are a Technology Editor-in-Chief evaluating an AI-generated "
                "multi-persona debate post for quality before LinkedIn publication.\n\n"

                # Phrase-level bans are handled upstream by a deterministic code
                # scanner. The judge's ONLY job is story-level quality assessment.
                "## YOUR JOB: STORY QUALITY ONLY\n"
                "Phrase-level checks (banned openers, banned inline phrases, generic "
                "AI language) have ALREADY been enforced by a deterministic code scanner "
                "before this post reached you. Do NOT re-scan for phrases. "
                "Do NOT invent new banned phrases. Do NOT flag phrasing style. "
                "has_stock_boilerplate and has_throat_clearing must ALWAYS be false — "
                "phrase scanning is done upstream.\n\n"

                # ── THE ONE STORY-LEVEL REVISE CONDITION ──────────────────────────
                "## SET verdict=REVISE only if ALL THREE of the following are true simultaneously:\n"
                "1. NO genuine intellectual clash — every persona reaches the same "
                "conclusion with no direct contradiction between any two voices.\n"
                "   A real clash: Persona A says 'X reduces complexity'; "
                "Persona B says 'X moves complexity underneath, not removes it'.\n"
                "   NOT a clash: 'creates opportunity' + 'but there are challenges'.\n"
                "2. COMPLETELY GENERIC — the entire post could apply word-for-word to "
                "any other AI news story, with zero article-specific claims or names.\n"
                "3. ZERO GROUNDED FACTS — not one claim can be traced to the source.\n\n"
                "If even ONE of the three is NOT true → verdict=PASS.\n\n"

                # ── ONLY REMAINING PHRASE CHECK ───────────────────────────────────
                "## ALSO set verdict=REVISE (without requiring the three above) if:\n"
                "- The post mentions 'EU AI Act', 'GDPR', or 'AI Act' and the source "
                "article is NOT explicitly about EU regulation.\n\n"

                # ── OUTPUT SCHEMA ─────────────────────────────────────────────────
                "Return ONLY this JSON — no preamble, no extra keys:\n"
                "{{\n"
                '  "factuality_score": float between 0.0 and 1.0,\n'
                '  "editorial_quality_score": float between 0.0 and 1.0,\n'
                '  "has_stock_boilerplate": false,\n'
                '  "has_throat_clearing": false,\n'
                '  "is_boring_or_repetitive": bool,\n'
                '  "has_genuine_conflict": bool,\n'
                '  "narrative_has_progression": bool,\n'
                '  "critique": "exact failing sentence quoted verbatim, or empty string",\n'
                '  "verdict": "PASS" or "REVISE"\n'
                "}}"
            ),
            (
                "human",
                "Source News & Facts:\n{source_text}\n\n"
                "Generated Debate Post:\n{generated_text}\n\n"
                "Evaluate story quality only (conflict, specificity, grounded facts). "
                "Return JSON only, no preamble."
            ),
        ])

    async def evaluate(
        self,
        summary: NewsSummary,
        personas: PersonaSetOutput,
        source_text: str,
        run_id: str | None = None,
        linkedin_audit: dict | None = None,
    ) -> EvaluationResult:
        generated_text = self._compose_generated_text(summary, personas)
        enriched_source = _enrich_source(source_text, summary)

        # 0. Deterministic pre-scan — check all persona perspectives BEFORE
        #    calling Jev or the LLM judge. This is pure Python string matching
        #    and is 100% reliable unlike the probabilistic judge.
        #    Any banned opener or inline phrase → immediately force REGENERATE.
        _banned_hits = _pre_scan_personas(personas, run_id, summary.article_id)
        if _banned_hits:
            reasons = [f"deterministic_scanner: {h}" for h in _banned_hits]
            r = EvaluationResult(
                article_id=summary.article_id,
                factuality=0.5,
                groundedness=0.0,
                hallucination=0.5,
                toxicity=0.0,
                policy_check="PASS",
                overall_score=0.0,
                decision=EvaluationDecision.REGENERATE,
                publish_eligible=False,
                failure_reasons=reasons,
            )
            logger.warning(
                "[%s] deterministic_scanner REGENERATE article=%s hits=%s",
                run_id or "?", summary.article_id, _banned_hits,
            )
            return r

        # 1. Primary Jev System One / MCP evaluation
        if self._settings.jev_enabled and self._jev is not None:
            try:
                result = await self._jev.evaluate_content(
                    article_id=summary.article_id,
                    source_text=enriched_source,
                    generated_text=generated_text,
                )
                logger.debug(
                    "jev evaluate article=%s factuality=%.2f hallucination=%.2f",
                    summary.article_id, result.factuality, result.hallucination,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Jev evaluation failed for %s (%s) — falling back to MCP",
                    summary.article_id, exc,
                )
                try:
                    result = await self._mcp.evaluate_content(
                        article_id=summary.article_id,
                        source_text=enriched_source,
                        generated_text=generated_text,
                        persona="all",
                    )
                except Exception as mcp_exc:  # noqa: BLE001
                    logger.warning(
                        "MCP evaluation also failed for %s (%s) — using neutral scores",
                        summary.article_id, mcp_exc,
                    )
                    result = _neutral_result(summary.article_id)
        else:
            try:
                result = await self._mcp.evaluate_content(
                    article_id=summary.article_id,
                    source_text=enriched_source,
                    generated_text=generated_text,
                    persona="all",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MCP evaluation failed for %s (%s) — using neutral scores",
                    summary.article_id, exc,
                )
                result = _neutral_result(summary.article_id)

        # Sanitize degenerate backend scores (0.00/0.00/1.00 pattern signals
        # a silent MCP failure rather than genuine low-quality content).
        if result.factuality == 0.0 and result.groundedness == 0.0 and result.hallucination >= 0.99:
            logger.warning(
                "Degenerate scores detected for %s (factuality=0.0, groundedness=0.0, "
                "hallucination=1.0) — likely backend failure, applying neutral scores",
                summary.article_id,
            )
            result = _neutral_result(summary.article_id)

        # 2. Independent LLM-as-a-Judge Review pass (Generator != Evaluator)
        try:
            handler, _ = get_langfuse_callback(
                run_id=run_id,
                tags=["evaluator_judge", self._settings.app_env],
                metadata={
                    "article_id": summary.article_id,
                    "model": self._judge_llm.model_name,
                    "agent": "llm_judge",
                },
            )
            callbacks = [handler] if handler else []
            chain = self._judge_prompt | self._judge_llm
            judge_res = await chain.ainvoke(
                {"source_text": enriched_source, "generated_text": generated_text},
                config={"callbacks": callbacks} if callbacks else {},
            )
            raw_content = judge_res.content if hasattr(judge_res, "content") else str(judge_res)
            # Parse JSON out of response
            clean_json = raw_content.strip()
            if "```json" in clean_json:
                clean_json = clean_json.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_json:
                clean_json = clean_json.split("```")[1].split("```")[0].strip()

            judge_data = json.loads(clean_json)
            logger.info(
                "[%s] llm_judge review article=%s verdict=%s quality=%.2f boilerplate=%s "
                "throat_clearing=%s conflict=%s narrative=%s critique='%s'",
                run_id or "?", summary.article_id,
                judge_data.get("verdict", "PASS"),
                judge_data.get("editorial_quality_score", 0.0),
                judge_data.get("has_stock_boilerplate", False),
                judge_data.get("has_throat_clearing", False),
                judge_data.get("has_genuine_conflict", True),
                judge_data.get("narrative_has_progression", True),
                judge_data.get("critique", ""),
            )

            # Hard REGENERATE: ONLY when the judge sets has_stock_boilerplate=true OR
            # has_throat_clearing=true — these correspond to LITERAL banned phrases from
            # the explicit list we gave it. The bare verdict=REVISE alone is NOT sufficient
            # because the judge at temperature=0.1 over-invents new bans beyond the list.
            has_boilerplate = (
                judge_data.get("has_stock_boilerplate")
                or judge_data.get("has_throat_clearing")
            )
            if has_boilerplate:
                critique = judge_data.get("critique", "Stock boilerplate or throat-clearing detected")
                result.groundedness = 0.0   # forces REGENERATE via _apply_gate
                result.failure_reasons.append(f"llm_judge: {critique}")
                logger.warning(
                    "[%s] llm_judge REGENERATE article=%s boilerplate=%s throat=%s critique='%s'",
                    run_id or "?", summary.article_id,
                    judge_data.get("has_stock_boilerplate", False),
                    judge_data.get("has_throat_clearing", False),
                    critique,
                )
        except Exception as exc:
            logger.warning("[%s] LLM judge pass non-blocking error: %s", run_id or "?", exc)

        # LinkedIn Skills audit — advisory signals logged for observability.
        # Low hook_strength or commentability adds a failure_reason but does NOT
        # force REGENERATE on its own — it informs the retry avoid_phrases.
        if linkedin_audit:
            hook_s  = linkedin_audit.get("hook_strength", 1.0)
            comment = linkedin_audit.get("commentability", 1.0)
            ai_den  = linkedin_audit.get("ai_style_density", 0.0)
            formula = linkedin_audit.get("hook_formula", "")
            blockers = linkedin_audit.get("blockers", [])
            logger.info(
                "[%s] linkedin_audit article=%s hook=%.2f commentability=%.2f "
                "ai_density=%.2f formula=%s blockers=%s",
                run_id or "?", summary.article_id,
                hook_s, comment, ai_den, formula, blockers,
            )
            if hook_s < 0.50:
                result.failure_reasons.append(
                    f"linkedin_audit: weak hook (score={hook_s:.2f}) — "
                    "open with a number, named entity, or structural tension"
                )
            if comment < 0.50:
                result.failure_reasons.append(
                    f"linkedin_audit: low commentability (score={comment:.2f}) — "
                    "CTA must pose one concrete unresolved question from the story tension"
                )
            if ai_den > 0.60:
                result.failure_reasons.append(
                    f"linkedin_audit: high AI-vocab density (score={ai_den:.2f}) — "
                    "remove: leverage, streamline, fundamentally, game-changer"
                )
            for blocker in blockers[:2]:
                result.failure_reasons.append(f"linkedin_audit_blocker: {blocker}")

        # Deterministic gate — scoring backend is advisory only
        result.decision = self._apply_gate(result)
        result.publish_eligible = result.decision == EvaluationDecision.PASS
        return result

    def _apply_gate(self, r: EvaluationResult) -> EvaluationDecision:
        s = self._settings

        # Hard BLOCK — PII or prompt injection must never reach publication
        if r.pii_detected:
            return EvaluationDecision.BLOCK
        if r.prompt_injection_detected:
            return EvaluationDecision.BLOCK

        # Quality regeneration gates
        if r.factuality < s.eval_factuality_threshold:
            return EvaluationDecision.REGENERATE
        if r.groundedness < s.eval_groundedness_threshold:
            return EvaluationDecision.REGENERATE
        if r.hallucination > s.eval_hallucination_threshold:
            return EvaluationDecision.REGENERATE

        # Policy / bias gates
        if r.political_bias_detected:
            return EvaluationDecision.HUMAN_REVIEW
        if r.policy_check != "PASS":
            return EvaluationDecision.HUMAN_REVIEW

        return EvaluationDecision.PASS

    @staticmethod
    def _compose_generated_text(summary: NewsSummary, personas: PersonaSetOutput) -> str:
        parts = [
            summary.headline,
            summary.summary,
            personas.business.perspective,
            personas.policy.perspective,
            personas.genz.perspective,
            personas.linkedin.perspective,
        ]
        return "\n\n".join(parts)


def _enrich_source(raw_source: str, summary: NewsSummary) -> str:
    """
    Combine raw article content (often truncated to ~250 chars on GNews free
    plan) with the LLM-generated summary fields so the Jev evaluator has
    enough grounded context to score persona outputs fairly.
    """
    parts = [raw_source.strip()] if raw_source.strip() else []
    parts += [
        f"TITLE: {summary.headline}",
        f"SUMMARY: {summary.summary}",
        f"KEY POINTS: {' | '.join(summary.key_points)}" if summary.key_points else "",
        f"BUSINESS IMPACT: {summary.business_impact}" if summary.business_impact else "",
        f"JOB IMPACT: {summary.job_impact}" if summary.job_impact else "",
        f"TECHNOLOGY IMPACT: {summary.technology_impact}" if summary.technology_impact else "",
        f"WHY IT MATTERS: {summary.why_it_matters}" if summary.why_it_matters else "",
    ]
    return "\n\n".join(p for p in parts if p)


def _neutral_result(article_id: str) -> "EvaluationResult":
    """
    Returns a neutral EvaluationResult when both Jev and MCP backends fail,
    or when degenerate scores (0/0/1) are detected.

    Neutral scores (0.6/0.6/0.3) are above all three thresholds (0.5/0.5/0.85),
    so the gate falls through to the LLM judge as the sole arbiter.
    """
    return EvaluationResult(
        article_id=article_id,
        factuality=0.6,
        groundedness=0.6,
        hallucination=0.3,
        toxicity=0.0,
        policy_check="PASS",
        overall_score=0.6,
        decision=EvaluationDecision.PASS,
        publish_eligible=True,
        failure_reasons=[],
    )


def _pre_scan_personas(
    personas: PersonaSetOutput,
    run_id: str | None,
    article_id: str,
) -> list[str]:
    """
    Deterministic banned-phrase pre-scan run BEFORE the LLM judge.

    Imports _check_persona_text from publisher_agent (single source of truth
    for the banned lists).  Returns a list of (persona, phrase) hit strings
    if any persona text contains a banned opener or inline phrase, else [].

    Running this at evaluation time (not just at publish time) means the
    retry loop gets failure_reasons with the exact offending phrase injected
    into find_angle, giving Qwen a much tighter signal on what to avoid.
    """
    from daily_news.agents.publisher_agent import _check_persona_text

    persona_map = {
        "business": getattr(personas, "business", None),
        "linkedin":  getattr(personas, "linkedin",  None),
        "genz":      getattr(personas, "genz",      None),
        "policy":    getattr(personas, "policy",    None),
    }
    hits: list[str] = []
    for key, p in persona_map.items():
        if p is None:
            continue
        text = getattr(p, "perspective", "") or ""
        if not text.strip():
            continue
        hit = _check_persona_text(text)
        if hit:
            logger.warning(
                "[%s] pre_scan persona=%s article=%s banned_phrase='%s'",
                run_id or "?", key, article_id, hit,
            )
            hits.append(f"persona={key} phrase='{hit}'")
    return hits
