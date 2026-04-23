"""Core retrieval logic for RAG agent.

Provides `retrieve_documents()` which searches FAQs and documents using
the hybrid search pipeline. Used by the LangGraph graph and API surfaces.
"""

import asyncio
import logging

from langchain_core.runnables import RunnableConfig

from ..configuration import Configuration
from .vector_search import (
    faq_hybrid_search_async,
    fetch_parent_chunks_async,
    hybrid_search_async,
    multi_query_hybrid_search_async,
)

logger = logging.getLogger(__name__)


def format_faq_as_xml(faq: dict) -> str:
    """Format a single FAQ as XML string."""
    return f'''<faq id="{faq['id']}">
<question>{faq['question']}</question>
<answer>{faq['answer']}</answer>
</faq>'''


def format_doc_as_xml(doc: dict, idx: int, parent: dict | None = None) -> str:
    """Format a document as XML string, optionally with parent context."""
    content = doc.get("content", "")
    heading = doc.get("heading", "")

    parts = [f'<document id="{idx}"']
    if heading:
        parts.append(f' heading="{heading}"')
    if doc.get("hierarchy_path"):
        parts.append(f' path="{doc["hierarchy_path"]}"')
    parts.append('>')

    if parent:
        parent_heading = parent.get("heading", "")
        parent_content = parent.get("content", "")
        parts.append(f'\n<parent_context heading="{parent_heading}">\n{parent_content}\n</parent_context>')

    parts.append(f'\n{content}\n</document>')

    return "".join(parts)


async def retrieve_documents(query: str, config: RunnableConfig) -> dict:
    """Core retrieval logic - searches FAQs and documents.

    Args:
        query: Search query string
        config: RunnableConfig with company/retrieval settings

    Returns:
        Dict with 'documents' (list[str]), 'sources' (list[dict]), 'summary' (str)
    """
    configuration = Configuration.from_runnable_config(config)
    company = configuration.company
    limit = configuration.retrieval_limit
    faq_enabled = configuration.faq_enabled
    faq_threshold = configuration.faq_confidence_threshold

    logger.info("=" * 80)
    logger.info(f"[retrieve] query='{query[:50]}...', company={company}, limit={limit}, faq_enabled={faq_enabled}")
    logger.info(f"[retrieve] FULL QUERY: '{query}'")

    try:
        # Step 1: Run FAQ + Document search in parallel (#1 speed optimization)
        async def _search_documents():
            if configuration.query_rewrite_enabled:
                from .query_rewriter import rewrite_queries
                from .vector_search import _executor, get_dense_embedding

                loop = asyncio.get_running_loop()

                # Pipeline: embed original query while rewriting (#7)
                original_embed_task = loop.run_in_executor(
                    _executor, get_dense_embedding, query
                )
                rewrite_task = rewrite_queries(query, configuration.query_rewrite_model)
                original_dense, queries = await asyncio.gather(
                    original_embed_task, rewrite_task
                )

                results = await multi_query_hybrid_search_async(
                    queries=queries,
                    company=company,
                    limit=limit,
                    first_dense_embedding=original_dense,
                )
                logger.info(f"[retrieve] Multi-query document search returned {len(results)} results")
                return results
            else:
                results = await hybrid_search_async(
                    query=query,
                    company=company,
                    limit=limit,
                )
                logger.info(f"[retrieve] Document search returned {len(results)} results")
                return results

        if faq_enabled and faq_threshold > 0:
            faq_results, doc_results = await asyncio.gather(
                faq_hybrid_search_async(query=query, company=company, limit=3),
                _search_documents(),
            )
            logger.info(f"[retrieve] FAQ search returned {len(faq_results)} results")
        else:
            faq_results = []
            doc_results = await _search_documents()

        # Step 2: Check for high-confidence FAQ match
        high_confidence_faq = None
        if faq_results:
            top_faq = faq_results[0]
            logger.info(f"[retrieve] Top FAQ score: {top_faq.get('relevance_score', 0):.3f}")

            if top_faq.get("relevance_score", 0) >= faq_threshold:
                high_confidence_faq = top_faq
                logger.info("[retrieve] High-confidence FAQ found, returning directly")

        # Step 3a: If high-confidence FAQ, return it directly (doc_results discarded)
        if high_confidence_faq:
            formatted = [format_faq_as_xml(high_confidence_faq)]
            sources = [{"type": "faq", "title": high_confidence_faq["question"]}]
            score = high_confidence_faq.get("relevance_score", 0)
            return {
                "documents": formatted,
                "sources": sources,
                "summary": f"Found FAQ with high confidence ({score:.2f})",
                "metrics": {
                    "faq_top_score": round(score, 2),
                    "faq_matched": True,
                    "faq_count": 1,
                    "doc_count": 0,
                    "doc_top_score": None,
                    "retrieval_strategy": "faq_direct",
                    "has_results": True,
                },
                "retrieval_details": [{
                    "type": "faq",
                    "title": high_confidence_faq["question"],
                    "relevance_score": round(score, 2),
                    "dense_score": round(high_confidence_faq.get("dense_score", 0), 3) if high_confidence_faq.get("dense_score") else None,
                    "sparse_score": round(high_confidence_faq.get("sparse_score", 0), 3) if high_confidence_faq.get("sparse_score") else None,
                    "hybrid_score": round(high_confidence_faq.get("hybrid_score", 0), 3) if high_confidence_faq.get("hybrid_score") else None,
                    "adopted": True,
                }],
            }

        # Step 3b: Use document results from parallel search

        # Step 4: Fetch parent chunks for context enrichment
        parent_chunks = {}
        if configuration.parent_context_enabled and doc_results:
            result_chunk_ids = {d.get("chunk_id") for d in doc_results if d.get("chunk_id")}
            parent_ids = {
                d.get("parent_chunk_id")
                for d in doc_results
                if d.get("parent_chunk_id") and d.get("parent_chunk_id") not in result_chunk_ids
            }
            if parent_ids:
                parent_chunks = await fetch_parent_chunks_async(list(parent_ids), company)
                logger.info(f"[retrieve] Fetched {len(parent_chunks)} parent chunks")

        # Step 5: Combine results
        formatted = []

        # Add FAQs as supplementary (if any with reasonable score)
        for faq in faq_results:
            if faq.get("relevance_score", 0) >= 0.5:
                formatted.append(format_faq_as_xml(faq))

        # Add documents (with parent context if available)
        for i, doc in enumerate(doc_results, 1):
            parent = parent_chunks.get(doc.get("parent_chunk_id"))
            formatted.append(format_doc_as_xml(doc, i, parent))

        if not formatted:
            logger.info("[retrieve] No results found")
            return {
                "documents": [],
                "sources": [],
                "summary": "No documents or FAQs found",
                "metrics": {
                    "faq_top_score": round(faq_results[0].get("relevance_score", 0), 2) if faq_results else None,
                    "faq_matched": False,
                    "faq_count": 0,
                    "doc_count": 0,
                    "doc_top_score": None,
                    "retrieval_strategy": "multi_query" if configuration.query_rewrite_enabled else "single",
                    "has_results": False,
                },
                "retrieval_details": [],
            }

        faq_count = len([f for f in faq_results if f.get("relevance_score", 0) >= 0.5])
        logger.info(f"[retrieve] Returning {faq_count} FAQs + {len(doc_results)} documents")

        sources = []
        for faq in faq_results:
            if faq.get("relevance_score", 0) >= 0.5:
                sources.append({"type": "faq", "title": faq["question"]})
        for doc in doc_results:
            sources.append({"type": "document", "title": doc.get("heading") or doc.get("original_filename", "문서")})

        doc_scores = [round(d.get("relevance_score", 0), 3) for d in doc_results[:3]]
        faq_top = round(faq_results[0].get("relevance_score", 0), 2) if faq_results else None

        retrieval_details = [
            {
                "type": "faq",
                "title": faq["question"],
                "relevance_score": round(faq.get("relevance_score", 0), 2),
                "dense_score": round(faq.get("dense_score", 0), 3) if faq.get("dense_score") else None,
                "sparse_score": round(faq.get("sparse_score", 0), 3) if faq.get("sparse_score") else None,
                "hybrid_score": round(faq.get("hybrid_score", 0), 3) if faq.get("hybrid_score") else None,
                "adopted": True,
            }
            for faq in faq_results
            if faq.get("relevance_score", 0) >= 0.5
        ] + [
            {
                "type": "document",
                "title": doc.get("heading") or doc.get("original_filename", "문서"),
                "relevance_score": round(doc.get("relevance_score", 0), 3),
                "dense_score": round(doc.get("dense_score", 0), 3) if doc.get("dense_score") else None,
                "sparse_score": round(doc.get("sparse_score", 0), 3) if doc.get("sparse_score") else None,
                "hybrid_score": round(doc.get("hybrid_score", 0), 3) if doc.get("hybrid_score") else None,
                "original_filename": doc.get("original_filename"),
                "hierarchy_path": doc.get("hierarchy_path"),
                "adopted": True,
            }
            for doc in doc_results
        ]
        return {
            "documents": formatted,
            "sources": sources,
            "summary": f"Retrieved {faq_count} FAQs and {len(doc_results)} documents",
            "metrics": {
                "faq_top_score": faq_top,
                "faq_matched": False,
                "faq_count": faq_count,
                "doc_count": len(doc_results),
                "doc_top_score": doc_scores[0] if doc_scores else None,
                "doc_scores": doc_scores,
                "retrieval_strategy": "multi_query" if configuration.query_rewrite_enabled else "single",
                "has_results": True,
            },
            "retrieval_details": retrieval_details,
        }

    except Exception as e:
        logger.error(f"[retrieve] Error: {e}", exc_info=True)
        return {
            "documents": [],
            "sources": [],
            "summary": f"Error: {e!s}",
            "metrics": {"has_results": False, "error": str(e)[:100]},
            "retrieval_details": [],
        }
