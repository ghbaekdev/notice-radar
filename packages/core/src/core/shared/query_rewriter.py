"""Query rewriting for improved retrieval.

Transforms conversational user queries into multiple search-optimized queries
using an LLM (gpt-5.4-nano). Produces the original query plus 3 rewritten variants
to improve recall via multi-query hybrid search.

Uses raw OpenAI API (not LangChain) to avoid LangGraph Platform's callback
propagation, which would stream rewrite results to the frontend.
"""

import logging

import openai
from langsmith import traceable

logger = logging.getLogger(__name__)


QUERY_REWRITE_PROMPT = """당신은 검색 쿼리 최적화 전문가입니다.
사용자 질문을 벡터 검색에 최적화된 검색 쿼리로 변환하세요.

규칙:
1. 한국어 질문을 3개의 검색 쿼리로 변환하세요.
2. 각 쿼리는 원래 질문의 다른 측면이나 다른 표현을 사용하세요.
3. 대화체 표현을 제거하고 핵심 키워드 중심으로 작성하세요.
4. 약어가 있으면 풀네임도 포함하세요 (예: RPA → Robotic Process Automation, RPA).
5. 각 쿼리는 한 줄로, 번호 없이 줄바꿈으로 구분하세요.

사용자 질문: {question}

검색 쿼리 3개:"""


@traceable(name="query_rewrite", run_type="chain")
async def rewrite_queries(question: str, model: str) -> list[str]:
    """Generate multiple search queries from a user question.

    Uses the raw OpenAI API (bypassing LangChain) to produce 3 search-optimized
    query variants. This prevents LangGraph Platform from intercepting the LLM
    call and streaming rewrite results to the frontend.

    Args:
        question: Original user question
        model: OpenAI model name (e.g., "gpt-4o-mini")

    Returns:
        List of query strings: [original, variant1, variant2, variant3]
    """
    client = openai.AsyncOpenAI()

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": QUERY_REWRITE_PROMPT.format(question=question)}
        ],
        temperature=0.0,
    )

    content = response.choices[0].message.content or ""
    rewritten = [q.strip() for q in content.strip().split("\n") if q.strip()]

    # Always include the original query as well
    all_queries = [question, *rewritten[:3]]

    logger.info(f"[query_rewrite] Original: '{question[:50]}...' -> {len(all_queries)} queries")
    for i, q in enumerate(all_queries):
        logger.info(f"[query_rewrite]   [{i}] {q[:80]}")

    return all_queries
