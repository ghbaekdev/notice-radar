"""API Config 관리 라우터"""
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.database.repository import ApiConfigRepository

from ..dependencies import get_current_company

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api-configs", tags=["api-configs"])


class ApiConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, description="API 설정 이름")
    endpoint: str = Field(..., description="API 엔드포인트 URL")
    method: str = Field("POST", description="HTTP 메소드")
    headers: dict[str, Any] | None = Field(default_factory=dict)
    auth_type: str | None = Field("none", description="인증 타입: none, bearer, api_key, basic")
    auth_config: dict[str, Any] | None = Field(default_factory=dict)
    timeout_seconds: float | None = Field(30.0)
    request_template: dict[str, Any] | None = Field(default_factory=dict)
    response_mapping: dict[str, Any] | None = Field(default_factory=dict)


class ApiConfigUpdate(BaseModel):
    name: str | None = None
    endpoint: str | None = None
    method: str | None = None
    headers: dict[str, Any] | None = None
    auth_type: str | None = None
    auth_config: dict[str, Any] | None = None
    timeout_seconds: float | None = None
    request_template: dict[str, Any] | None = None
    response_mapping: dict[str, Any] | None = None


@router.post("")
async def create_api_config(
    data: ApiConfigCreate,
    company: dict = Depends(get_current_company)
):
    """API 설정 생성"""
    repo = ApiConfigRepository()
    config = await repo.create(company_id=company["id"], data=data.model_dump())
    return config


@router.get("")
async def list_api_configs(
    company: dict = Depends(get_current_company)
):
    """회사별 API 설정 목록"""
    repo = ApiConfigRepository()
    return await repo.get_by_company(company["id"])


@router.get("/{config_id}")
async def get_api_config(
    config_id: UUID,
    company: dict = Depends(get_current_company)
):
    """단일 API 설정 조회"""
    repo = ApiConfigRepository()
    config = await repo.get_by_id(config_id)
    if not config or config["company_id"] != company["id"]:
        raise HTTPException(status_code=404, detail="API config not found")
    return config


@router.put("/{config_id}")
async def update_api_config(
    config_id: UUID,
    data: ApiConfigUpdate,
    company: dict = Depends(get_current_company)
):
    """API 설정 수정"""
    repo = ApiConfigRepository()
    existing = await repo.get_by_id(config_id)
    if not existing or existing["company_id"] != company["id"]:
        raise HTTPException(status_code=404, detail="API config not found")

    update_data = data.model_dump(exclude_unset=True)
    config = await repo.update(config_id, **update_data)
    return config


@router.delete("/{config_id}")
async def delete_api_config(
    config_id: UUID,
    company: dict = Depends(get_current_company)
):
    """API 설정 삭제"""
    repo = ApiConfigRepository()
    existing = await repo.get_by_id(config_id)
    if not existing or existing["company_id"] != company["id"]:
        raise HTTPException(status_code=404, detail="API config not found")

    await repo.delete(config_id)
    return {"message": "API config deleted", "id": str(config_id)}
