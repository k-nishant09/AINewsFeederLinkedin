"""
PageIndex MCP Server — vectorless document indexing and reasoning-based retrieval.

Documents are stored as structured trees (sections) in memory.
Production: back this with PostgreSQL or object storage.

No embedding model. No vector database.
Retrieval is query-driven section selection using an LLM reasoner.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer as FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("PageIndex MCP Server")

# In-process document store — replace with PostgreSQL for production
_document_store: dict[str, dict[str, Any]] = {}


def _split_into_sections(content: str) -> list[dict[str, str]]:
    """Split article content into logical sections by paragraph."""
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    sections = []
    for i, paragraph in enumerate(paragraphs):
        sections.append(
            {
                "section_id": f"s{i:03d}",
                "heading": f"Section {i + 1}",
                "content": paragraph,
            }
        )
    return sections


def _score_section(section: str, question: str) -> float:
    """
    Simple keyword overlap score.
    Production: replace with LLM-based reasoning or BM25.
    """
    q_words = set(question.lower().split())
    s_words = set(section.lower().split())
    overlap = len(q_words & s_words)
    return overlap / max(len(q_words), 1)


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def pageindex_index_document(
    document_id: str,
    title: str,
    content: str,
    source_url: str,
) -> dict:
    """Index a document into the PageIndex document tree."""
    sections = _split_into_sections(content)
    _document_store[document_id] = {
        "document_id": document_id,
        "title": title,
        "source_url": source_url,
        "sections": sections,
        "full_content": content,
    }
    logger.info("Indexed document %s with %d sections", document_id, len(sections))
    return {"document_id": document_id, "sections_count": len(sections), "status": "indexed"}


@mcp.tool()
async def pageindex_get_relevant_sections(document_id: str, question: str) -> dict:
    """Retrieve the most relevant document sections for a given question (vectorless)."""
    doc = _document_store.get(document_id)
    if not doc:
        return {"document_id": document_id, "sections_text": "", "error": "Document not found"}

    scored = [
        (section, _score_section(section["content"], question))
        for section in doc["sections"]
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_sections = [s for s, _ in scored[:5]]
    sections_text = "\n\n".join(
        f"[{s['heading']}]\n{s['content']}" for s in top_sections
    )
    return {
        "document_id": document_id,
        "question": question,
        "sections_text": sections_text,
        "sections_count": len(top_sections),
    }


@mcp.tool()
async def pageindex_search_document(document_id: str, query: str) -> dict:
    """Search a document for relevant content."""
    return await pageindex_get_relevant_sections(document_id, query)


@mcp.tool()
async def pageindex_get_document(document_id: str) -> dict:
    """Retrieve full document metadata."""
    doc = _document_store.get(document_id)
    if not doc:
        return {"error": f"Document {document_id!r} not found"}
    return {
        "document_id": doc["document_id"],
        "title": doc["title"],
        "source_url": doc["source_url"],
        "sections_count": len(doc["sections"]),
    }


# ── Tool registry ─────────────────────────────────────────────────────────────
_TOOLS: dict[str, Any] = {
    "pageindex_index_document":       pageindex_index_document,
    "pageindex.index_document":       pageindex_index_document,
    "pageindex_get_relevant_sections": pageindex_get_relevant_sections,
    "pageindex.get_relevant_sections": pageindex_get_relevant_sections,
    "pageindex_search_document":      pageindex_search_document,
    "pageindex.search_document":      pageindex_search_document,
    "pageindex_get_document":         pageindex_get_document,
    "pageindex.get_document":         pageindex_get_document,
}

# ── App assembly ──────────────────────────────────────────────────────────────

_app = FastAPI()


@_app.get("/health")
def health():
    return {"status": "healthy", "server": "pageindex-mcp", "documents": len(_document_store), "tools": list(_TOOLS)}


@_app.post("/call")
async def call_tool(request: dict):
    """Simple REST tool dispatcher. Body: {"tool": "<name>", "arguments": {...}}"""
    import inspect

    from fastapi import HTTPException
    tool_name = request.get("tool", "")
    fn = _TOOLS.get(tool_name)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name!r}. Available: {list(_TOOLS)}")
    result = await fn(**request.get("arguments", {})) if inspect.iscoroutinefunction(fn) else fn(**request.get("arguments", {}))
    return {"result": result}


_app.mount("/mcp", mcp.streamable_http_app())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(_app, host="0.0.0.0", port=8000)
