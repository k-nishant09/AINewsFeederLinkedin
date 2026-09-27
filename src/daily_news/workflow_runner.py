"""CLI entry point — invoked by the OpenShift CronJob."""
from __future__ import annotations

import asyncio
import logging
import sys

from daily_news.mcp.client import mcp_factory
from daily_news.mcp.jev_client import JevClient
from daily_news.observability.tracing import flush_langfuse, setup_tracing
from daily_news.workflows.daily_news_graph import daily_news_graph, make_initial_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def run():
    # Initialise OTLP + Langfuse before graph.ainvoke()
    setup_tracing()

    state = make_initial_state()
    logger.info("Starting daily news workflow — run_id=%s", state["run_id"])

    try:
        final = await daily_news_graph.ainvoke(state)
    finally:
        # Always flush Langfuse so traces are delivered even on error/SIGTERM
        flush_langfuse()
        # Drain all persistent HTTP connection pools — prevents ResourceWarning
        # and ensures in-flight keep-alive sockets are closed cleanly on pod shutdown.
        await mcp_factory().aclose()
        # Close the Jev client pool if Jev was enabled this run
        from daily_news.config.settings import get_settings
        if get_settings().jev_base_url:
            try:
                await JevClient().aclose()
            except Exception:  # noqa: BLE001
                pass  # not critical — OS will reclaim sockets at process exit anyway

    errors = final.get("errors", [])
    published = len(final.get("linkedin_results", []))

    logger.info(
        "Workflow complete — status=%s published=%d errors=%d",
        final.get("workflow_status"),
        published,
        len(errors),
    )

    if errors:
        for err in errors:
            logger.warning("  error: %s", err)

    return 0 if not errors else 1


def main():
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
