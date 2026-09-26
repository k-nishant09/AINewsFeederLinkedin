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
from daily_news.mcp.evaluation import EvaluationMCPClient
from daily_news.mcp.jev_client import JevClient
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
        # Jev is the primary backend when JEV_BASE_URL is configured;
        # otherwise None and the fallback path goes straight to MCP.
        self._jev = JevClient() if s.jev_base_url else None
        self._mcp = EvaluationMCPClient()

        # Independent LLM-as-a-Judge Reviewer
        judge_model = s.eval_llm_model or s.llm_model
        judge_base_url = s.eval_llm_base_url or s.llm_base_url
        judge_api_key = s.eval_llm_api_key or s.llm_api_key

        self._judge_llm = ChatOpenAI(
            model=judge_model,
            api_key=judge_api_key,
            base_url=judge_base_url,
            temperature=0.1,  # low temperature for critical judgment
            http_client=httpx.Client(verify=False),
            http_async_client=httpx.AsyncClient(verify=False),
        )

        self._judge_prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "You are an elite Technology Editor-in-Chief and AI Content Judge. "
                "Your job is to rigorously evaluate generated debate posts before publication.\n\n"

                "## AUTOMATIC FAIL — set has_stock_boilerplate=true AND has_throat_clearing=true AND verdict=REVISE "
                "if ANY single one of the following patterns appears ANYWHERE in the post:\n\n"

                "### ANECDOTE / SETUP OPENERS (throat-clearing) — INSTANT FAIL:\n"
                "- 'When I was scaling', 'When I was running', 'When I was building', 'When I was at'\n"
                "- 'When we deployed', 'When we rolled out', 'When we launched', 'When we built', 'When we were'\n"
                "- 'Imagine you are', \"Imagine you're\", 'Imagine a startup', 'Imagine running', 'Imagine you had'\n"
                "- 'Consider a scenario', 'Let me paint a picture', 'Let me be clear', \"Let's dive in\", 'Let us dive in'\n"
                "- Any persona opening with 'I' as the FIRST word of their passage\n\n"

                "### BANNED INLINE PHRASES — INSTANT FAIL:\n"
                "- ANY phrase matching 'the real <word>' pattern: "
                "'the real question', 'the real challenge', 'the real issue', 'the real problem', "
                "'the real bottleneck', 'the real shift', 'the real implication', 'the real concern', "
                "'the real business', 'the real risk', 'the real opportunity', 'the real test', etc.\n"
                "- ANY phrase starting with 'in the end': 'in the end,', 'in the end this', 'in the end the'\n"
                "- 'the challenge lies in', 'the key challenge is', 'the key metric is'\n"
                "- 'sounds great, but', 'sounds promising, but', 'sounds great on paper', 'sounds promising on paper'\n"
                "- 'sounds revolutionary', 'sounds like a dream', 'sounds like a game-changer'\n"
                "- 'at the end of the day', 'it remains to be seen', 'only time will tell'\n"
                "- 'what remains to be seen', 'the potential here is', 'the promise is great'\n"
                "- 'more tools do not always', 'my advice to'\n\n"

                "### WRONG-CONTEXT REGULATION — INSTANT FAIL:\n"
                "- 'Under the EU AI Act', 'Under GDPR', 'Under the AI Act' unless the article is explicitly about that regulation\n"
                "- 'The General Data Protection Regulation', 'GDPR mandates', 'EU AI Act requires' unless article is about GDPR/EU AI Act\n\n"

                "## ALSO REJECT (set verdict=REVISE) if:\n"
                "1. All personas reach the same conclusion — no genuine intellectual clash between at least 2.\n"
                "2. Any persona uses generic AI truisms not anchored to the SPECIFIC article announced.\n"
                "3. The entire post could apply word-for-word to any other AI news story.\n"
                "4. Any factual claim goes beyond what the source article states.\n\n"

                "## PASS requires ALL of:\n"
                "- Zero banned openers or inline phrases (scan every word)\n"
                "- Every persona opens with a concrete, article-specific claim — NOT a setup story or anecdote\n"
                "- Genuine disagreement between at least 2 personas\n"
                "- Every factual claim traceable to the source article\n\n"

                "Return a JSON object with this EXACT schema — no extra keys:\n"
                "{{\n"
                '  "factuality_score": float,\n'
                '  "editorial_quality_score": float,\n'
                '  "has_stock_boilerplate": bool,\n'
                '  "has_throat_clearing": bool,\n'
                '  "is_boring_or_repetitive": bool,\n'
                '  "critique": "name the exact offending phrase or sentence",\n'
                '  "verdict": "PASS" or "REVISE"\n'
                "}}"
            ),
            (
                "human",
                "Source News & Facts:\n{source_text}\n\n"
                "Generated Debate Post:\n{generated_text}\n\n"
                "IMPORTANT: Scan line by line for banned phrases FIRST. "
                "A single banned phrase is an automatic REVISE — do not average it out. "
                "Return JSON only, no preamble."
            ),
        ])

    async def evaluate(
        self,
        summary: NewsSummary,
        personas: PersonaSetOutput,
        source_text: str,
        run_id: str | None = None,
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
                result = await self._mcp.evaluate_content(
                    article_id=summary.article_id,
                    source_text=enriched_source,
                    generated_text=generated_text,
                    persona="all",
                )
        else:
            result = await self._mcp.evaluate_content(
                article_id=summary.article_id,
                source_text=enriched_source,
                generated_text=generated_text,
                persona="all",
            )

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
                "[%s] llm_judge review article=%s verdict=%s quality=%.2f boilerplate=%s throat_clearing=%s critique='%s'",
                run_id or "?", summary.article_id,
                judge_data.get("verdict", "PASS"),
                judge_data.get("editorial_quality_score", 0.0),
                judge_data.get("has_stock_boilerplate", False),
                judge_data.get("has_throat_clearing", False),
                judge_data.get("critique", ""),
            )

            # Hard REGENERATE: stock boilerplate, throat-clearing, or judge REVISE verdict
            # forces groundedness below the regeneration threshold so _apply_gate returns REGENERATE.
            # The judge (Qwen @ 0.1) sees only the assembled post — never its own generation context.
            has_boilerplate = (
                judge_data.get("has_stock_boilerplate")
                or judge_data.get("has_throat_clearing")
                or judge_data.get("verdict") == "REVISE"
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


def _pre_scan_personas(
    personas: "PersonaSetOutput",
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
