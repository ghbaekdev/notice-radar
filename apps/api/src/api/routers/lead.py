"""리드 등록 + Slack 알림 통합 라우터"""
import logging
import os
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from core.database.repository import LeadRepository

from ..dependencies import get_current_company

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lead", tags=["lead"])

HQ_API_BASE_URL = os.getenv("HQ_API_BASE_URL", "https://hq-api.aiu-ai-dev.com")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "C07U04050GP")  # dev/qa: C07U04050GP, prd: C014KUTP1FB

SOURCE_NAME_HQ_MAP = {
    "CHATBOT": "AGENT_CHATBOT",
    "INBOUND": "AGENT_INBOUND",
    "OUTBOUND": "AGENT_OUTBOUND",
}


class LeadRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lead_name: str = Field(..., alias="leadName", description="업체명")
    representative_name: str = Field(..., alias="representativeName", description="대표자명")
    representative_phone_number: str = Field(..., alias="representativePhoneNumber", description="대표 전화번호")
    region_name: str | None = Field(None, alias="regionName", description="대략적 지역 (예: 성남시 분당구)")
    source_name: str = Field("CHATBOT", alias="sourceName", description="리드 출처 (CHATBOT, INBOUND, OUTBOUND)")


class LeadRegisterResponse(BaseModel):
    success: bool
    hq_result: dict[str, Any] | None = None
    hq_error: str | None = None
    slack_sent: bool = False
    slack_error: str | None = None


class LeadListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    lead_name: str
    representative_name: str
    representative_phone: str
    region_name: str | None
    source_name: str = "CHATBOT"
    slack_sent: bool
    created_at: datetime
    updated_at: datetime | None = None


class LeadListResponse(BaseModel):
    items: list[LeadListItem]
    total: int
    page: int
    limit: int


def _build_slack_blocks(data: LeadRegisterRequest) -> list[dict]:
    """기존 Recatch Form Submission 형식에 맞춘 Block Kit 구조"""
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Form Submission", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "(AI Agent)_도입문의"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Customer Name:*\n{data.representative_name}"},
                {"type": "mrkdwn", "text": f"*Phone:*\n{data.representative_phone_number}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*사업자명 (개인/법인):*\n{data.lead_name}"},
        },
    ]
    if data.region_name:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*지역명:*\n{data.region_name}"},
        })
    return blocks


@router.post("/register", response_model=LeadRegisterResponse)
async def register_lead(data: LeadRegisterRequest):
    """리드를 HQ API에 등록하고 Slack으로 알림을 전송합니다."""
    response_data = LeadRegisterResponse(success=False)

    # 1. HQ API 호출
    hq_url = f"{HQ_API_BASE_URL}/lead/recatch/register"
    hq_body = data.model_dump(exclude_none=True, by_alias=True)
    hq_body["sourceName"] = SOURCE_NAME_HQ_MAP.get(data.source_name, f"AGENT_{data.source_name}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            hq_response = await client.post(
                hq_url,
                json=hq_body,
                headers={"Content-Type": "application/json"},
            )
            hq_response.raise_for_status()
            response_data.success = True
            response_data.hq_result = hq_response.json()
            logger.info(f"리드 등록 성공: {data.lead_name}")
    except httpx.HTTPStatusError as e:
        error_body = e.response.text
        response_data.hq_error = f"HQ API {e.response.status_code}: {error_body}"
        logger.error(f"리드 등록 실패: {response_data.hq_error}")
        return response_data
    except httpx.RequestError as e:
        response_data.hq_error = f"HQ API 연결 오류: {e!s}"
        logger.error(f"리드 등록 실패: {response_data.hq_error}")
        return response_data

    # 2. Slack 알림 (chat.postMessage)
    if SLACK_BOT_TOKEN:
        try:
            blocks = _build_slack_blocks(data)
            async with httpx.AsyncClient(timeout=10.0) as client:
                slack_response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    json={
                        "channel": SLACK_CHANNEL_ID,
                        "blocks": blocks,
                        "text": f"새 리드 등록: {data.lead_name}",  # fallback
                    },
                    headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                )
                slack_result = slack_response.json()
                if slack_result.get("ok"):
                    response_data.slack_sent = True
                    logger.info("Slack 알림 전송 성공")
                else:
                    response_data.slack_error = f"Slack API 오류: {slack_result.get('error')}"
                    logger.warning(f"Slack 알림 실패: {response_data.slack_error}")
        except Exception as e:
            response_data.slack_error = f"Slack 전송 실패: {e!s}"
            logger.warning(f"Slack 알림 실패: {response_data.slack_error}")

    # 3. DB 저장 (HQ 응답 + Slack 결과 포함)
    try:
        repo = LeadRepository()
        await repo.create({
            "lead_name": data.lead_name,
            "representative_name": data.representative_name,
            "representative_phone": data.representative_phone_number,
            "region_name": data.region_name,
            "source_name": data.source_name,
            "hq_response": response_data.hq_result or {},
            "slack_sent": response_data.slack_sent,
        })
    except Exception as e:
        logger.error(f"리드 DB 저장 실패: {e}")

    return response_data


@router.get("", response_model=LeadListResponse)
async def list_leads(
    page: int = Query(1, ge=1, description="페이지 번호"),
    limit: int = Query(20, ge=1, le=100, description="페이지당 항목 수"),
    search: str | None = Query(None, description="업체명 검색"),
    company: dict = Depends(get_current_company),
):
    """리드 목록 조회 (페이지네이션 + 검색)"""
    repo = LeadRepository()
    offset = (page - 1) * limit

    leads = await repo.get_all(
        limit=limit,
        offset=offset,
        search=search,
    )
    total = await repo.count_all(search)

    return LeadListResponse(
        items=[LeadListItem(**lead) for lead in leads],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{lead_id}", response_model=LeadListItem)
async def get_lead(
    lead_id: UUID,
    company: dict = Depends(get_current_company),
):
    """리드 상세 조회"""
    repo = LeadRepository()
    lead = await repo.get_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    return LeadListItem(**lead)


@router.delete("/{lead_id}")
async def delete_lead(
    lead_id: UUID,
    company: dict = Depends(get_current_company),
):
    """리드 삭제"""
    repo = LeadRepository()
    lead = await repo.get_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    await repo.delete(lead_id)
    return {"message": "Lead deleted", "id": str(lead_id)}
