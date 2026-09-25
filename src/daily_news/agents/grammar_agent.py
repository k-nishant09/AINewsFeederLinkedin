"""
Grammar Agent — post-composition proofreading pass.

Runs after _compose_main_post() and before LinkedIn publish.
Fixes spelling, grammar, punctuation, and capitalisation errors in the
composed post text without changing tone, structure, or meaning.

This is a lightweight LLM pass (temperature=0, low token cost).
It does NOT rewrite the post — it corrects it.

Wired in as a final step inside PublisherAgent.publish() via
PublisherAgent.grammar_check().  Can also be used standalone.
"""
from __future__ import annotations

import logging

import httpx
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from daily_news.config.settings import get_settings
from daily_news.observability.tracing import get_langfuse_callback

log = logging.getLogger(__name__)


def _load_prompt(name: str) -> str:
    from pathlib import Path
    p = Path(__file__).parent.parent.parent.parent / "prompts" / f"{name}.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


class GrammarAgent:
    """
    Proofread a composed LinkedIn post for spelling, grammar, and punctuation.

    Returns the corrected text.  If the LLM call fails for any reason, returns
    the original text unchanged — this agent is best-effort, not blocking.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        self._llm = ChatOpenAI(
            model=s.llm_model,
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            temperature=0.0,   # deterministic — correction, not generation
            http_client=httpx.Client(verify=False),
            http_async_client=httpx.AsyncClient(verify=False),
        )
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", _load_prompt("grammar")),
            (
                "human",
                "Proofread the following LinkedIn post and return only the corrected text.\n\n"
                "{post_text}",
            ),
        ])

    async def correct(
        self,
        post_text: str,
        run_id: str | None = None,
        article_id: str | None = None,
    ) -> str:
        """
        Return a grammar-corrected version of *post_text*.

        On any error (network, parse, timeout) returns *post_text* unchanged.
        """
        if not post_text or not post_text.strip():
            return post_text

        handler, _ = get_langfuse_callback(
            run_id=run_id,
            tags=["grammar", self._settings.app_env],
            metadata={
                "article_id": article_id or "unknown",
                "model":      self._settings.llm_model,
                "agent":      "grammar_agent",
            },
        )
        callbacks = [handler] if handler else []

        try:
            chain = self._prompt | self._llm
            result = await chain.ainvoke(
                {"post_text": post_text},
                config={"callbacks": callbacks} if callbacks else {},
            )
            corrected = result.content.strip() if hasattr(result, "content") else str(result).strip()
            if corrected:
                log.info(
                    "[%s] grammar_agent: corrected %d → %d chars",
                    run_id or "?", len(post_text), len(corrected),
                )
                return corrected
            # Empty output — return original
            log.warning("[%s] grammar_agent: empty output — using original", run_id or "?")
            return post_text

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[%s] grammar_agent: failed (%s) — using original post text unchanged",
                run_id or "?", exc,
            )
            return post_text
