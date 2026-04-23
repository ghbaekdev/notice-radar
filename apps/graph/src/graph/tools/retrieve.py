"""Retrieve tool for RAG agent with FAQ priority.

Exposes two things:
- `retrieve_documents()`: re-exported from core for backward compatibility
- `tools`: list of LangChain tool objects for bind_tools() schema generation
"""

from typing import Annotated

from langchain_core.tools import tool

from core.shared.retrieve import retrieve_documents

# Re-export for use in nodes
__all__ = ["retrieve", "retrieve_documents", "tools"]


@tool
async def retrieve(
    query: Annotated[str, "The search query to find relevant documents"],
) -> str:
    """Search for relevant FAQs and documents in the company knowledge base.

    FAQ answers are prioritized when confidence is high (>= 0.85).
    Otherwise, documents are searched and FAQs are included as supplementary context.
    """
    # This function body is never called directly.
    # It exists only to generate the tool schema for bind_tools().
    # The actual execution is done via retrieve_documents() in agent_tools node.
    return "Use retrieve_documents() instead"


# Export tools list for bind_tools() in agent_generate
tools = [retrieve]
