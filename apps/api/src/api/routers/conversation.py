"""대화 로그 관리 라우터"""
import json
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator

from core.database.repository import ConversationRepository

from ..dependencies import get_current_company

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/conversations", tags=["conversations"])

# =============================================================================
# Pydantic Models
# =============================================================================

class ConversationMessageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    role: str
    content: str
    sources: list = []
    execution_trace: list | None = None
    created_at: datetime

    @field_validator("sources", mode="before")
    @classmethod
    def parse_sources(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("execution_trace", mode="before")
    @classmethod
    def parse_execution_trace(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v

class ConversationListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    thread_id: str
    source: str
    message_count: int
    first_message: str | None
    created_at: datetime
    updated_at: datetime

class ConversationDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    thread_id: str
    source: str
    message_count: int
    first_message: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageResponse]

class ConversationListResponse(BaseModel):
    items: list[ConversationListItem]
    total: int
    page: int
    limit: int

# =============================================================================
# Endpoints
# =============================================================================

@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    page: int = Query(1, ge=1, description="페이지 번호"),
    limit: int = Query(20, ge=1, le=100, description="페이지당 항목 수"),
    search: str | None = Query(None, description="첫 메시지 검색"),
    company: dict = Depends(get_current_company)
):
    """대화 목록 조회 (페이지네이션 + 검색)"""
    repo = ConversationRepository()
    offset = (page - 1) * limit

    conversations = await repo.get_by_company(
        company_id=company["id"],
        limit=limit,
        offset=offset,
        search=search
    )
    total = await repo.count_by_company(company["id"], search)

    return ConversationListResponse(
        items=[ConversationListItem(**conv) for conv in conversations],
        total=total,
        page=page,
        limit=limit
    )

@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    company: dict = Depends(get_current_company)
):
    """대화 상세 조회 (메시지 포함)"""
    repo = ConversationRepository()
    conv = await repo.get_by_id(conversation_id)
    if not conv or conv["company_id"] != company["id"]:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await repo.get_messages(conversation_id)

    return ConversationDetail(
        **conv,
        messages=[ConversationMessageResponse(**msg) for msg in messages]
    )

@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    company: dict = Depends(get_current_company)
):
    """대화 삭제"""
    repo = ConversationRepository()
    conv = await repo.get_by_id(conversation_id)
    if not conv or conv["company_id"] != company["id"]:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await repo.delete(conversation_id)
    return {"message": "Conversation deleted", "id": str(conversation_id)}
