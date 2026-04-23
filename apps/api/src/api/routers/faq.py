"""FAQ 관리 라우터"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.database.repository import FAQRepository
from core.shared.vector_search import (
    delete_faq_from_qdrant,
    faq_hybrid_search,
    init_faq_collection,
    store_faq_in_qdrant,
)

from ..dependencies import get_current_company

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/faqs", tags=["faqs"])

# =============================================================================
# Pydantic Models
# =============================================================================

class FAQCreate(BaseModel):
    question: str = Field(..., min_length=1, description="FAQ 질문")
    answer: str = Field(..., min_length=1, description="FAQ 답변")

class FAQUpdate(BaseModel):
    question: str | None = Field(None, min_length=1)
    answer: str | None = Field(None, min_length=1)
    is_active: bool | None = None

class FAQResponse(BaseModel):
    id: UUID
    company_id: UUID
    question: str
    answer: str
    is_active: bool

class FAQSearchResult(BaseModel):
    id: str
    question: str
    answer: str
    relevance_score: float
    dense_score: float | None = None
    sparse_score: float | None = None
    hybrid_score: float | None = None

# =============================================================================
# Endpoints
# =============================================================================

@router.post("", response_model=FAQResponse)
async def create_faq(
    data: FAQCreate,
    company: dict = Depends(get_current_company)
):
    """FAQ 생성 및 벡터 저장"""
    logger.info("=" * 80)
    logger.info(f"[FAQ] Creating FAQ for company: {company['name']}")

    repo = FAQRepository()
    faq = await repo.create(
        company_id=company["id"],
        question=data.question,
        answer=data.answer
    )

    # Qdrant에 저장
    try:
        init_faq_collection(company["name"])
        store_faq_in_qdrant(
            faq_id=str(faq["id"]),
            question=faq["question"],
            answer=faq["answer"],
            company=company["name"]
        )
        logger.info(f"[FAQ] Stored in Qdrant: {faq['id']}")
    except Exception as e:
        logger.error(f"[FAQ] Qdrant storage failed: {e}", exc_info=True)

    return FAQResponse(**faq)

@router.get("", response_model=list[FAQResponse])
async def list_faqs(
    is_active: bool | None = Query(None, description="활성 상태 필터"),
    company: dict = Depends(get_current_company)
):
    """회사별 FAQ 목록 조회"""
    repo = FAQRepository()
    faqs = await repo.get_by_company(company["id"], is_active)
    return [FAQResponse(**faq) for faq in faqs]

@router.get("/search", response_model=list[FAQSearchResult])
async def search_faqs(
    q: str = Query(..., min_length=1, description="검색 쿼리"),
    limit: int = Query(3, ge=1, le=10, description="결과 수"),
    company: dict = Depends(get_current_company)
):
    """FAQ 벡터 검색 테스트"""
    logger.info(f"[FAQ] Search query: {q}")
    results = faq_hybrid_search(q, company["name"], limit)
    return [FAQSearchResult(**r) for r in results]

@router.get("/{faq_id}", response_model=FAQResponse)
async def get_faq(
    faq_id: UUID,
    company: dict = Depends(get_current_company)
):
    """단일 FAQ 조회"""
    repo = FAQRepository()
    faq = await repo.get_by_id(faq_id)
    if not faq or faq["company_id"] != company["id"]:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return FAQResponse(**faq)

@router.put("/{faq_id}", response_model=FAQResponse)
async def update_faq(
    faq_id: UUID,
    data: FAQUpdate,
    company: dict = Depends(get_current_company)
):
    """FAQ 수정 및 벡터 재저장"""
    repo = FAQRepository()
    existing = await repo.get_by_id(faq_id)
    if not existing or existing["company_id"] != company["id"]:
        raise HTTPException(status_code=404, detail="FAQ not found")

    update_data = data.model_dump(exclude_unset=True)
    faq = await repo.update(faq_id, **update_data)

    # question이나 answer가 변경되면 Qdrant 재저장
    if "question" in update_data or "answer" in update_data:
        try:
            delete_faq_from_qdrant(str(faq_id), company["name"])
            store_faq_in_qdrant(
                faq_id=str(faq["id"]),
                question=faq["question"],
                answer=faq["answer"],
                company=company["name"]
            )
            logger.info(f"[FAQ] Re-indexed in Qdrant: {faq_id}")
        except Exception as e:
            logger.error(f"[FAQ] Qdrant re-index failed: {e}", exc_info=True)

    return FAQResponse(**faq)

@router.delete("/{faq_id}")
async def delete_faq(
    faq_id: UUID,
    company: dict = Depends(get_current_company)
):
    """FAQ 삭제"""
    repo = FAQRepository()
    existing = await repo.get_by_id(faq_id)
    if not existing or existing["company_id"] != company["id"]:
        raise HTTPException(status_code=404, detail="FAQ not found")

    await repo.delete(faq_id)

    try:
        delete_faq_from_qdrant(str(faq_id), company["name"])
        logger.info(f"[FAQ] Deleted from Qdrant: {faq_id}")
    except Exception as e:
        logger.error(f"[FAQ] Qdrant deletion failed: {e}", exc_info=True)

    return {"message": "FAQ deleted", "id": str(faq_id)}

@router.post("/reindex")
async def reindex_faqs(
    company: dict = Depends(get_current_company)
):
    """전체 FAQ 재인덱싱"""
    logger.info("=" * 80)
    logger.info(f"[FAQ] Reindexing all FAQs for company: {company['name']}")

    repo = FAQRepository()
    faqs = await repo.get_by_company(company["id"], is_active=True)

    init_faq_collection(company["name"], force_recreate=True)

    success = 0
    failed = 0
    for faq in faqs:
        try:
            store_faq_in_qdrant(
                faq_id=str(faq["id"]),
                question=faq["question"],
                answer=faq["answer"],
                company=company["name"]
            )
            success += 1
        except Exception as e:
            logger.error(f"[FAQ] Reindex failed for {faq['id']}: {e}")
            failed += 1

    return {"message": "Reindex complete", "success": success, "failed": failed}
