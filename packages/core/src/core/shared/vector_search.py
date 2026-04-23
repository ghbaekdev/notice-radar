"""Shared hybrid search module for RAG agent and document API.

Consolidates vector search functionality from retrieval.py and document.py:
- Dense embeddings via Gemini (gemini-embedding-001)
- Sparse embeddings via BM25 (fastembed)
- Hybrid scoring: dense_weight * dense + sparse_weight * sparse
- Cohere reranking (rerank-multilingual-v3.0)
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from fastembed import SparseTextEmbedding
from google import genai
from langchain_cohere import CohereRerank
from langchain_core.documents import Document
from langsmith import traceable
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    Modifier,
    PointIdsList,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

logger = logging.getLogger(__name__)

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

DENSE_WEIGHT = 0.7
SPARSE_WEIGHT = 0.3
EXPECTED_FAQ_DENSE_SIZE = 3072

# Sparse embedding model (lazy init)
_sparse_model = None

# Qdrant client singleton (lazy init)
_qdrant_client = None

# Thread pool for async execution
_executor = ThreadPoolExecutor(max_workers=4)


def get_qdrant_client() -> QdrantClient:
    """Reusable Qdrant client singleton (TCP connection reuse)."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    return _qdrant_client


def get_sparse_model() -> SparseTextEmbedding:
    """BM25 sparse embedding model (lazy initialization)."""
    global _sparse_model
    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return _sparse_model


def get_collection_name(company: str) -> str:
    """Generate Qdrant collection name from company identifier."""
    normalized = company.lower().replace(" ", "_").replace("-", "_")
    return f"documents_{normalized}"


def get_faq_collection_name(company: str) -> str:
    """Generate Qdrant FAQ collection name from company identifier."""
    normalized = company.lower().replace(" ", "_").replace("-", "_")
    return f"faqs_{normalized}"


@traceable(name="dense_embedding", run_type="embedding")
def get_dense_embedding(query: str, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
    """Get dense embedding from Gemini.

    Args:
        query: Text to embed
        task_type: Gemini task type - "RETRIEVAL_QUERY" for search queries,
                   "RETRIEVAL_DOCUMENT" for document indexing
    """
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    gemini_client = genai.Client(api_key=gemini_api_key)
    return gemini_client.models.embed_content(
        model="gemini-embedding-001",
        contents=query,
        config={"task_type": task_type},
    ).embeddings[0].values


@traceable(name="dense_embedding_batch", run_type="embedding")
def get_dense_embeddings_batch(queries: list[str], task_type: str = "RETRIEVAL_QUERY") -> list[list[float]]:
    """Get dense embeddings for multiple queries in a single API call.

    Args:
        queries: List of texts to embed
        task_type: Gemini task type

    Returns:
        List of embedding vectors, one per query.
    """
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    gemini_client = genai.Client(api_key=gemini_api_key)
    result = gemini_client.models.embed_content(
        model="gemini-embedding-001",
        contents=queries,
        config={"task_type": task_type},
    )
    return [emb.values for emb in result.embeddings]


def get_sparse_embeddings(texts: list[str]) -> list[tuple[list[int], list[float]]]:
    """Generate BM25 sparse embeddings for texts.

    Returns list of (indices, values) tuples for each text.
    """
    model = get_sparse_model()
    embeddings = list(model.embed(texts))
    return [(emb.indices.tolist(), emb.values.tolist()) for emb in embeddings]


@traceable(name="cohere_rerank", run_type="chain")
def rerank_results(query: str, docs: list[Document], limit: int) -> list[Document]:
    """Rerank documents using Cohere."""
    cohere_api_key = os.getenv("COHERE_API_KEY")
    if not cohere_api_key:
        raise RuntimeError("COHERE_API_KEY not configured")

    reranker = CohereRerank(
        cohere_api_key=cohere_api_key,
        model="rerank-multilingual-v3.0",
        top_n=limit,
    )
    return reranker.compress_documents(docs, query)


@traceable(name="hybrid_search", run_type="retriever")
def hybrid_search(query: str, company: str, limit: int = 5) -> list[dict]:
    """
    Hybrid search: Dense (Gemini, 0.7) + Sparse (BM25, 0.3) + Cohere Rerank.

    Args:
        query: Search query string
        company: Company/tenant identifier
        limit: Maximum number of results to return

    Returns:
        List of dicts with keys: content, heading, hierarchy_path, relevance_score.
    """
    collection_name = get_collection_name(company)

    # 1. Query embeddings
    query_dense = get_dense_embedding(query)

    sparse_model = get_sparse_model()
    sparse_embs = list(sparse_model.embed([query]))
    query_sparse_indices = sparse_embs[0].indices.tolist()
    query_sparse_values = sparse_embs[0].values.tolist()

    # 2. Qdrant search (dense + sparse)
    qdrant = get_qdrant_client()

    dense_results = qdrant.query_points(
        collection_name=collection_name,
        query=query_dense,
        using="dense",
        limit=50,
        with_payload=True,
    ).points

    sparse_results = qdrant.query_points(
        collection_name=collection_name,
        query=SparseVector(indices=query_sparse_indices, values=query_sparse_values),
        using="sparse",
        limit=50,
        with_payload=True,
    ).points

    logger.info(f"[hybrid_search] Dense: {len(dense_results)}, Sparse: {len(sparse_results)}")
    if dense_results:
        logger.info(f"[hybrid_search] Top dense score: {dense_results[0].score:.4f}, heading: {dense_results[0].payload.get('heading', '')[:50]}")
    if sparse_results:
        logger.info(f"[hybrid_search] Top sparse score: {sparse_results[0].score:.4f}, heading: {sparse_results[0].payload.get('heading', '')[:50]}")

    # 3. Combine scores
    combined = {}
    for r in dense_results:
        combined[r.id] = {
            "dense_score": r.score,
            "sparse_score": 0.0,
            "payload": r.payload,
        }
    for r in sparse_results:
        if r.id in combined:
            combined[r.id]["sparse_score"] = r.score
        else:
            combined[r.id] = {
                "dense_score": 0.0,
                "sparse_score": r.score,
                "payload": r.payload,
            }

    for data in combined.values():
        data["hybrid_score"] = DENSE_WEIGHT * data["dense_score"] + SPARSE_WEIGHT * data["sparse_score"]

    sorted_results = sorted(combined.items(), key=lambda x: x[1]["hybrid_score"], reverse=True)

    for i, (_pid, data) in enumerate(sorted_results[:5]):
        heading = data["payload"].get("heading", "N/A")[:40] if data["payload"] else "N/A"
        logger.info(f"[hybrid_search] Top {i+1}: hybrid={data['hybrid_score']:.4f} dense={data['dense_score']:.4f} sparse={data['sparse_score']:.4f} | {heading}")

    if not sorted_results:
        return []

    # 4. Cohere Rerank (top 20 -> top limit)
    docs_for_rerank = [
        Document(
            page_content=data["payload"].get("content", "") if data["payload"] else "",
            metadata={
                "heading": data["payload"].get("heading") if data["payload"] else None,
                "hierarchy_path": data["payload"].get("hierarchy_path") if data["payload"] else None,
                "original_filename": data["payload"].get("original_filename") if data["payload"] else None,
                "chunk_id": data["payload"].get("chunk_id") if data["payload"] else None,
                "document_id": data["payload"].get("document_id") if data["payload"] else None,
                "level": data["payload"].get("level") if data["payload"] else None,
                "order": data["payload"].get("order") if data["payload"] else None,
                "parent_chunk_id": data["payload"].get("parent_chunk_id") if data["payload"] else None,
                "dense_score": data["dense_score"],
                "sparse_score": data["sparse_score"],
                "hybrid_score": data["hybrid_score"],
            },
        )
        for _, data in sorted_results[:20]
    ]

    reranked_docs = rerank_results(query, docs_for_rerank, limit)

    logger.info(f"[hybrid_search] Reranked: {len(reranked_docs)} results")
    for i, doc in enumerate(reranked_docs):
        logger.info(f"[hybrid_search] Reranked {i+1}: relevance={doc.metadata.get('relevance_score', 0):.4f} | {doc.metadata.get('heading', '')[:40]}")

    results = []
    for doc in reranked_docs:
        results.append({
            "content": doc.page_content,
            "heading": doc.metadata.get("heading", ""),
            "hierarchy_path": doc.metadata.get("hierarchy_path", ""),
            "original_filename": doc.metadata.get("original_filename", ""),
            "relevance_score": doc.metadata.get("relevance_score", 0),
            "chunk_id": doc.metadata.get("chunk_id"),
            "document_id": doc.metadata.get("document_id"),
            "level": doc.metadata.get("level"),
            "order": doc.metadata.get("order"),
            "parent_chunk_id": doc.metadata.get("parent_chunk_id"),
            "dense_score": doc.metadata.get("dense_score"),
            "sparse_score": doc.metadata.get("sparse_score"),
            "hybrid_score": doc.metadata.get("hybrid_score"),
        })

    return results


async def hybrid_search_async(
    query: str,
    company: str,
    limit: int = 5
) -> list[dict]:
    """Async wrapper for hybrid_search using ThreadPoolExecutor.

    Args:
        query: Search query string
        company: Company/tenant identifier
        limit: Maximum number of results

    Returns:
        List of document dicts with content, heading, etc.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        hybrid_search,
        query,
        company,
        limit,
    )


def fetch_parent_chunks(parent_chunk_ids: list[str], company: str) -> dict[str, dict]:
    """Fetch parent chunks from Qdrant by chunk_id.

    Args:
        parent_chunk_ids: List of parent chunk IDs to fetch
        company: Company/tenant identifier

    Returns:
        Dict mapping chunk_id -> {chunk_id, heading, content, level, hierarchy_path}
    """
    if not parent_chunk_ids:
        return {}

    collection_name = get_collection_name(company)
    qdrant = get_qdrant_client()

    results = qdrant.scroll(
        collection_name=collection_name,
        scroll_filter=Filter(
            should=[
                FieldCondition(key="chunk_id", match=MatchValue(value=cid))
                for cid in parent_chunk_ids
            ]
        ),
        limit=len(parent_chunk_ids) + 5,
        with_payload=True,
        with_vectors=False,
    )

    chunks = {}
    for point in results[0]:
        payload = point.payload
        chunk_id = payload.get("chunk_id")
        if chunk_id:
            chunks[chunk_id] = {
                "chunk_id": chunk_id,
                "heading": payload.get("heading"),
                "content": payload.get("content"),
                "level": payload.get("level"),
                "hierarchy_path": payload.get("hierarchy_path"),
            }

    logger.info(f"[fetch_parent_chunks] Fetched {len(chunks)}/{len(parent_chunk_ids)} parent chunks")
    return chunks


async def fetch_parent_chunks_async(parent_chunk_ids: list[str], company: str) -> dict[str, dict]:
    """Async wrapper for fetch_parent_chunks."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        fetch_parent_chunks,
        parent_chunk_ids,
        company,
    )


@traceable(name="multi_query_hybrid_search", run_type="retriever")
def multi_query_hybrid_search(
    queries: list[str],
    company: str,
    limit: int = 5,
    first_dense_embedding: list[float] | None = None,
) -> list[dict]:
    """
    Run hybrid search for multiple query variants and merge results via RRF.

    Uses reciprocal rank fusion (RRF) to combine results from different queries,
    then applies Cohere reranking on the merged set.

    Args:
        queries: List of query strings (original + rewritten variants)
        company: Company/tenant identifier
        limit: Maximum number of final results
        first_dense_embedding: Pre-computed dense embedding for queries[0],
            enabling pipelining with query rewrite (#7).

    Returns:
        List of dicts with keys: content, heading, hierarchy_path, relevance_score.
    """
    collection_name = get_collection_name(company)
    qdrant = get_qdrant_client()

    # 1. Batch dense embeddings — use pre-computed first if available (#7 pipelining)
    if first_dense_embedding is not None and len(queries) > 1:
        remaining_dense = get_dense_embeddings_batch(queries[1:])
        all_dense_embeddings = [first_dense_embedding, *remaining_dense]
    else:
        all_dense_embeddings = get_dense_embeddings_batch(queries)

    # 2. Batch sparse embeddings — local BM25, already fast
    sparse_model = get_sparse_model()
    all_sparse_embeddings = list(sparse_model.embed(queries))

    # 3. Run Qdrant searches in parallel across all queries (#2)
    def _search_query(qi: int):
        dense_results = qdrant.query_points(
            collection_name=collection_name,
            query=all_dense_embeddings[qi],
            using="dense",
            limit=30,
            with_payload=True,
        ).points

        sparse_results = qdrant.query_points(
            collection_name=collection_name,
            query=SparseVector(
                indices=all_sparse_embeddings[qi].indices.tolist(),
                values=all_sparse_embeddings[qi].values.tolist(),
            ),
            using="sparse",
            limit=30,
            with_payload=True,
        ).points

        return dense_results, sparse_results

    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        search_results = list(pool.map(_search_query, range(len(queries))))

    # 4. RRF merge across all queries
    all_candidates = {}  # point_id -> {payload, rrf_score}

    for _qi, (dense_results, sparse_results) in enumerate(search_results):
        combined = {}
        for r in dense_results:
            combined[r.id] = {"dense_score": r.score, "sparse_score": 0.0, "payload": r.payload}
        for r in sparse_results:
            if r.id in combined:
                combined[r.id]["sparse_score"] = r.score
            else:
                combined[r.id] = {"dense_score": 0.0, "sparse_score": r.score, "payload": r.payload}

        for data in combined.values():
            data["hybrid_score"] = DENSE_WEIGHT * data["dense_score"] + SPARSE_WEIGHT * data["sparse_score"]

        ranked = sorted(combined.items(), key=lambda x: x[1]["hybrid_score"], reverse=True)

        k = 60
        for rank, (point_id, data) in enumerate(ranked):
            rrf_score = 1.0 / (k + rank + 1)
            if point_id in all_candidates:
                all_candidates[point_id]["rrf_score"] += rrf_score
                if data["hybrid_score"] > all_candidates[point_id].get("hybrid_score", 0):
                    all_candidates[point_id]["dense_score"] = data["dense_score"]
                    all_candidates[point_id]["sparse_score"] = data["sparse_score"]
                    all_candidates[point_id]["hybrid_score"] = data["hybrid_score"]
            else:
                all_candidates[point_id] = {
                    "payload": data["payload"],
                    "rrf_score": rrf_score,
                    "dense_score": data["dense_score"],
                    "sparse_score": data["sparse_score"],
                    "hybrid_score": data["hybrid_score"],
                }

    logger.info(f"[multi_query_hybrid_search] {len(queries)} queries -> {len(all_candidates)} unique candidates")

    if not all_candidates:
        return []

    # Sort by RRF score and take top 20 for reranking
    sorted_candidates = sorted(all_candidates.items(), key=lambda x: x[1]["rrf_score"], reverse=True)

    docs_for_rerank = [
        Document(
            page_content=data["payload"].get("content", "") if data["payload"] else "",
            metadata={
                "heading": data["payload"].get("heading") if data["payload"] else None,
                "hierarchy_path": data["payload"].get("hierarchy_path") if data["payload"] else None,
                "original_filename": data["payload"].get("original_filename") if data["payload"] else None,
                "chunk_id": data["payload"].get("chunk_id") if data["payload"] else None,
                "document_id": data["payload"].get("document_id") if data["payload"] else None,
                "level": data["payload"].get("level") if data["payload"] else None,
                "order": data["payload"].get("order") if data["payload"] else None,
                "parent_chunk_id": data["payload"].get("parent_chunk_id") if data["payload"] else None,
                "dense_score": data.get("dense_score", 0),
                "sparse_score": data.get("sparse_score", 0),
                "hybrid_score": data.get("hybrid_score", 0),
            },
        )
        for _, data in sorted_candidates[:20]
    ]

    # Rerank using the ORIGINAL query (first in the list)
    reranked_docs = rerank_results(queries[0], docs_for_rerank, limit)

    logger.info(f"[multi_query_hybrid_search] Reranked: {len(reranked_docs)} results")

    results = []
    for doc in reranked_docs:
        results.append({
            "content": doc.page_content,
            "heading": doc.metadata.get("heading", ""),
            "hierarchy_path": doc.metadata.get("hierarchy_path", ""),
            "original_filename": doc.metadata.get("original_filename", ""),
            "relevance_score": doc.metadata.get("relevance_score", 0),
            "chunk_id": doc.metadata.get("chunk_id"),
            "document_id": doc.metadata.get("document_id"),
            "level": doc.metadata.get("level"),
            "order": doc.metadata.get("order"),
            "parent_chunk_id": doc.metadata.get("parent_chunk_id"),
            "dense_score": doc.metadata.get("dense_score"),
            "sparse_score": doc.metadata.get("sparse_score"),
            "hybrid_score": doc.metadata.get("hybrid_score"),
        })

    return results


async def multi_query_hybrid_search_async(
    queries: list[str],
    company: str,
    limit: int = 5,
    first_dense_embedding: list[float] | None = None,
) -> list[dict]:
    """Async wrapper for multi_query_hybrid_search."""
    from functools import partial

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        partial(
            multi_query_hybrid_search,
            queries,
            company,
            limit,
            first_dense_embedding=first_dense_embedding,
        ),
    )


def init_faq_collection(company: str, force_recreate: bool = False) -> None:
    """Initialize Qdrant collection for FAQs with same vector config as documents.

    Args:
        company: Company/tenant identifier
        force_recreate: If True, delete existing collection and recreate
    """
    collection_name = get_faq_collection_name(company)
    qdrant = get_qdrant_client()

    exists = qdrant.collection_exists(collection_name)

    if exists and not force_recreate:
        # 기존 컬렉션의 벡터 차원 검증
        info = qdrant.get_collection(collection_name)
        dense_config = info.config.params.vectors.get("dense")
        if dense_config and dense_config.size == EXPECTED_FAQ_DENSE_SIZE:
            logger.info(f"[init_faq_collection] Collection {collection_name} already exists with correct config")
            return
        # 차원 불일치 → 재생성 필요
        logger.warning(f"[init_faq_collection] Dimension mismatch in {collection_name} (expected {EXPECTED_FAQ_DENSE_SIZE}), recreating")
        force_recreate = True

    if exists and force_recreate:
        qdrant.delete_collection(collection_name)
        logger.info(f"[init_faq_collection] Deleted existing collection {collection_name}")

    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config={
            "dense": VectorParams(size=EXPECTED_FAQ_DENSE_SIZE, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(modifier=Modifier.IDF)
        },
    )

    logger.info(f"[init_faq_collection] Created collection {collection_name}")


@traceable(name="store_faq", run_type="chain")
def store_faq_in_qdrant(faq_id: str, question: str, answer: str, company: str) -> None:
    """Store FAQ with embeddings in Qdrant.

    Args:
        faq_id: Unique identifier for the FAQ
        question: Question text (used for embedding)
        answer: Answer text
        company: Company/tenant identifier
    """
    collection_name = get_faq_collection_name(company)

    # Generate embeddings from question
    dense_emb = get_dense_embedding(question)

    sparse_embs = get_sparse_embeddings([question])
    sparse_indices, sparse_values = sparse_embs[0]

    # Store in Qdrant
    qdrant = get_qdrant_client()

    qdrant.upsert(
        collection_name=collection_name,
        points=[
            PointStruct(
                id=faq_id,
                vector={
                    "dense": dense_emb,
                    "sparse": SparseVector(indices=sparse_indices, values=sparse_values),
                },
                payload={
                    "question": question,
                    "answer": answer,
                },
            )
        ],
    )

    logger.info(f"[store_faq_in_qdrant] Stored FAQ {faq_id} in {collection_name}")


def delete_faq_from_qdrant(faq_id: str, company: str) -> None:
    """Delete FAQ point from Qdrant.

    Args:
        faq_id: Unique identifier for the FAQ
        company: Company/tenant identifier
    """
    collection_name = get_faq_collection_name(company)
    qdrant = get_qdrant_client()

    qdrant.delete(
        collection_name=collection_name,
        points_selector=PointIdsList(points=[faq_id]),
    )

    logger.info(f"[delete_faq_from_qdrant] Deleted FAQ {faq_id} from {collection_name}")


@traceable(name="faq_hybrid_search", run_type="retriever")
def faq_hybrid_search(query: str, company: str, limit: int = 3) -> list[dict]:
    """
    Hybrid search for FAQs: Dense (Gemini, 0.7) + Sparse (BM25, 0.3) + Cohere Rerank.

    Args:
        query: Search query string
        company: Company/tenant identifier
        limit: Maximum number of results to return

    Returns:
        List of dicts with keys: id, question, answer, relevance_score.
    """
    collection_name = get_faq_collection_name(company)

    # 1. Query embeddings
    query_dense = get_dense_embedding(query)

    sparse_model = get_sparse_model()
    sparse_embs = list(sparse_model.embed([query]))
    query_sparse_indices = sparse_embs[0].indices.tolist()
    query_sparse_values = sparse_embs[0].values.tolist()

    # 2. Qdrant search (dense + sparse)
    qdrant = get_qdrant_client()

    try:
        dense_results = qdrant.query_points(
            collection_name=collection_name,
            query=query_dense,
            using="dense",
            limit=50,
            with_payload=True,
        ).points

        sparse_results = qdrant.query_points(
            collection_name=collection_name,
            query=SparseVector(indices=query_sparse_indices, values=query_sparse_values),
            using="sparse",
            limit=50,
            with_payload=True,
        ).points
    except UnexpectedResponse as e:
        if e.status_code == 404:
            logger.info(f"[faq_hybrid_search] Collection '{collection_name}' not found, skipping FAQ search")
            return []
        raise

    logger.info(f"[faq_hybrid_search] Dense: {len(dense_results)}, Sparse: {len(sparse_results)}")

    # 3. Combine scores
    combined = {}
    for r in dense_results:
        combined[r.id] = {
            "dense_score": r.score,
            "sparse_score": 0.0,
            "payload": r.payload,
        }
    for r in sparse_results:
        if r.id in combined:
            combined[r.id]["sparse_score"] = r.score
        else:
            combined[r.id] = {
                "dense_score": 0.0,
                "sparse_score": r.score,
                "payload": r.payload,
            }

    for data in combined.values():
        data["hybrid_score"] = DENSE_WEIGHT * data["dense_score"] + SPARSE_WEIGHT * data["sparse_score"]

    sorted_results = sorted(combined.items(), key=lambda x: x[1]["hybrid_score"], reverse=True)

    if not sorted_results:
        return []

    # 4. Cohere Rerank (top 20 -> top limit)
    docs_for_rerank = [
        Document(
            page_content=f"Q: {data['payload'].get('question', '')}\nA: {data['payload'].get('answer', '')}" if data["payload"] else "",
            metadata={
                "faq_id": faq_id,
                "question": data["payload"].get("question") if data["payload"] else None,
                "answer": data["payload"].get("answer") if data["payload"] else None,
                "dense_score": data["dense_score"],
                "sparse_score": data["sparse_score"],
                "hybrid_score": data["hybrid_score"],
            },
        )
        for faq_id, data in sorted_results[:20]
    ]

    reranked_docs = rerank_results(query, docs_for_rerank, limit)

    logger.info(f"[faq_hybrid_search] Reranked: {len(reranked_docs)} results")

    results = []
    for doc in reranked_docs:
        results.append({
            "id": doc.metadata.get("faq_id", ""),
            "question": doc.metadata.get("question", ""),
            "answer": doc.metadata.get("answer", ""),
            "relevance_score": doc.metadata.get("relevance_score", 0),
            "dense_score": doc.metadata.get("dense_score"),
            "sparse_score": doc.metadata.get("sparse_score"),
            "hybrid_score": doc.metadata.get("hybrid_score"),
        })

    return results


async def faq_hybrid_search_async(
    query: str,
    company: str,
    limit: int = 3
) -> list[dict]:
    """Async wrapper for faq_hybrid_search using ThreadPoolExecutor.

    Args:
        query: Search query string
        company: Company/tenant identifier
        limit: Maximum number of results

    Returns:
        List of FAQ dicts with id, question, answer, relevance_score.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        faq_hybrid_search,
        query,
        company,
        limit,
    )
