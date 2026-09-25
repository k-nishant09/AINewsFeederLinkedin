"""
Judgment Analysis Agent — Separates Facts, Reported Claims, Analysis, Unknowns,
and Boundaries (What NOT to conclude).

Provides the factual and epistemological guardrails for the MediaStorytellerAgent
and Persona agents, preventing LLMs from fabricating certainty or confusing
claims with verified facts.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from daily_news.config.settings import get_settings
from daily_news.models.intelligence import JudgmentAnalysis
from daily_news.observability.tracing import get_langfuse_callback

logger = logging.getLogger(__name__)


class _JudgmentAnalysisRaw(BaseModel):
    facts:                 list[str] = Field(default_factory=list)
    reported_claims:       list[str] = Field(default_factory=list)
    analysis_implications: list[str] = Field(default_factory=list)
    uncertainties:         list[str] = Field(default_factory=list)
    what_not_to_conclude:  list[str] = Field(default_factory=list)


_JUDGMENT_SYSTEM_PROMPT = """You are a rigorous investigative news analyst and epistemological editor.
Your job is to separate what is objectively factual from what is claimed, interpreted, or unknown.

Categories:
1. FACTS: Direct events, verified launches, official announcements, concrete numbers reported.
2. REPORTED CLAIMS: Statements made by company representatives or authors that represent their perspective/promises.
3. ANALYSIS IMPLICATIONS: Logical technical or economic consequences grounded in the evidence.
4. UNCERTAINTIES: What remains unproven, pending real-world benchmarks, or open to regulatory/market outcomes.
5. WHAT NOT TO CONCLUDE: Explicit boundaries of what should NOT be asserted as established fact.

Rules:
- Be concise, objective, and precise.
- Return output strictly conforming to the JSON schema.
"""


class JudgmentAgent:
    """Performs epistemological judgment analysis on article content and evidence."""

    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        self._llm = ChatOpenAI(
            model=s.llm_model,
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            temperature=0.2,  # Low temperature for strict factual boundaries
            http_client=httpx.Client(verify=False),
            http_async_client=httpx.AsyncClient(verify=False),
        )
        self._parser = PydanticOutputParser(pydantic_object=_JudgmentAnalysisRaw)
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", _JUDGMENT_SYSTEM_PROMPT),
            (
                "human",
                "Article ID: {article_id}\n\n"
                "Headline: {title}\n\n"
                "Source: {source}\n\n"
                "Content:\n{content}\n\n"
                "PageIndex Evidence:\n{pageindex_sections}\n\n"
                "{format_instructions}",
            ),
        ])

    async def analyze(
        self,
        article_id: str,
        title: str,
        source: str,
        content: str,
        pageindex_sections: str,
        run_id: str | None = None,
    ) -> JudgmentAnalysis:
        handler, _ = get_langfuse_callback(
            run_id=run_id,
            tags=["judgment_analysis", self._settings.app_env],
            metadata={"article_id": article_id, "source": source, "agent": "judgment_agent"},
        )
        callbacks = [handler] if handler else []

        try:
            chain = self._prompt | self._llm | self._parser
            raw: _JudgmentAnalysisRaw = await chain.ainvoke(
                {
                    "article_id":         article_id,
                    "title":              title,
                    "source":             source,
                    "content":            content[:3000],
                    "pageindex_sections": pageindex_sections or content[:2000],
                    "format_instructions": self._parser.get_format_instructions(),
                },
                config={"callbacks": callbacks} if callbacks else {},
            )
            return JudgmentAnalysis(
                facts=raw.facts,
                reported_claims=raw.reported_claims,
                analysis_implications=raw.analysis_implications,
                uncertainties=raw.uncertainties,
                what_not_to_conclude=raw.what_not_to_conclude,
            )
        except Exception as exc:
            logger.warning("JudgmentAgent failed for %s: %s — falling back to defaults", article_id, exc)
            return JudgmentAnalysis(
                facts=[title],
                reported_claims=[],
                analysis_implications=[],
                uncertainties=[],
                what_not_to_conclude=[],
            )
