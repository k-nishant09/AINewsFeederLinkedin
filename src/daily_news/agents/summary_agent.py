"""
Summary Agent — converts raw article + PageIndex context into structured NewsSummary.

Langfuse tracing
─────────────────
Every summarize() call creates a Langfuse trace with:
  - session_id  = run_id (groups all per-run traces together)
  - tags        = ["summary", "production"]
  - metadata    = article_id, source, model

Ref: https://langfuse.com/docs/integrations/langchain/tracing
"""
from __future__ import annotations

import httpx
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from daily_news.config.settings import get_settings
from daily_news.models.summary import NewsSummary
from daily_news.observability.tracing import get_langfuse_callback


def _load_prompt(name: str) -> str:
    from pathlib import Path
    # Prompts are at /app/prompts/ (repo root) — 4 levels up from this file:
    # /app/src/daily_news/agents/summary_agent.py → /app/
    p = Path(__file__).parent.parent.parent.parent / "prompts" / f"{name}.txt"
    return p.read_text(encoding="utf-8")


class SummaryAgent:
    """LangChain agent that produces structured NewsSummary from article content."""

    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        self._llm = ChatOpenAI(
            model=s.llm_model,
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            temperature=0.2,
            http_client=httpx.Client(verify=False),
            http_async_client=httpx.AsyncClient(verify=False),
        )
        self._parser = PydanticOutputParser(pydantic_object=NewsSummary)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", _load_prompt("summary")),
                (
                    "human",
                    "Article ID: {article_id}\n\n"
                    "Title: {title}\n\n"
                    "Source: {source} ({source_url})\n\n"
                    "Content:\n{content}\n\n"
                    "Relevant PageIndex Sections:\n{pageindex_sections}\n\n"
                    "{format_instructions}",
                ),
            ]
        )

    async def summarize(
        self,
        article_id: str,
        title: str,
        source: str,
        source_url: str,
        content: str,
        pageindex_sections: str,
        run_id: str | None = None,
    ) -> NewsSummary:
        # Langfuse v4 CallbackHandler — automatically traces all LLM calls,
        # prompts, token counts, and latency for this chain invocation.
        # Ref: https://langfuse.com/docs/integrations/langchain/tracing
        handler, _trace_id = get_langfuse_callback(
            run_id=run_id,
            tags=["summary", self._settings.app_env],
            metadata={
                "article_id": article_id,
                "source":     source,
                "model":      self._settings.llm_model,
                "agent":      "summary_agent",
            },
        )
        callbacks = [handler] if handler else []

        chain = self._prompt | self._llm | self._parser
        return await chain.ainvoke(
            {
                "article_id":           article_id,
                "title":                title,
                "source":               source,
                "source_url":           source_url,
                "content":              content,
                "pageindex_sections":   pageindex_sections,
                "format_instructions":  self._parser.get_format_instructions(),
            },
            config={"callbacks": callbacks} if callbacks else {},
        )
