"""Embed settings API endpoints."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db_pool
from core.utils import generate_api_key

from ..dependencies import get_current_company

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


# =============================================================================
# Pydantic Models
# =============================================================================

class EmbedSettings(BaseModel):
    embed_api_key: str | None = None
    embed_allowed_domains: list[str] = []
    embed_theme_color: str = "#3b82f6"
    embed_greeting: str = "안녕하세요! 무엇을 도와드릴까요?"
    embed_label: str = "채팅 상담"


class EmbedSettingsUpdate(BaseModel):
    embed_allowed_domains: list[str] | None = None
    embed_theme_color: str | None = None
    embed_greeting: str | None = None
    embed_label: str | None = None


# =============================================================================
# Repository (inline for simplicity)
# =============================================================================

class CompanySettingsRepository:
    """Repository for company_settings table operations"""

    async def get_by_company_id(self, company_id: UUID) -> dict | None:
        """Get settings for a company"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, company_id, embed_api_key, embed_allowed_domains,
                       embed_theme_color, embed_greeting, embed_label,
                       created_at, updated_at
                FROM company_settings
                WHERE company_id = $1
                """,
                company_id
            )
            return dict(row) if row else None

    async def create(self, company_id: UUID) -> dict:
        """Create settings with generated API key"""
        pool = get_db_pool()
        api_key = generate_api_key()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO company_settings (company_id, embed_api_key)
                VALUES ($1, $2)
                RETURNING id, company_id, embed_api_key, embed_allowed_domains,
                          embed_theme_color, embed_greeting, embed_label,
                          created_at, updated_at
                """,
                company_id,
                api_key,
            )
            logger.info(f"[CompanySettings] Created for company {company_id}")
            return dict(row)

    async def update(self, company_id: UUID, data: dict) -> dict | None:
        """Update settings for a company"""
        pool = get_db_pool()

        # Build dynamic SET clause
        set_parts = []
        values = []
        param_num = 1

        if "embed_allowed_domains" in data:
            set_parts.append(f"embed_allowed_domains = ${param_num}")
            values.append(data["embed_allowed_domains"])
            param_num += 1

        if "embed_theme_color" in data:
            set_parts.append(f"embed_theme_color = ${param_num}")
            values.append(data["embed_theme_color"])
            param_num += 1

        if "embed_greeting" in data:
            set_parts.append(f"embed_greeting = ${param_num}")
            values.append(data["embed_greeting"])
            param_num += 1

        if "embed_label" in data:
            set_parts.append(f"embed_label = ${param_num}")
            values.append(data["embed_label"])
            param_num += 1

        if not set_parts:
            return await self.get_by_company_id(company_id)

        set_parts.append("updated_at = NOW()")
        values.append(company_id)

        query = f"""
            UPDATE company_settings
            SET {', '.join(set_parts)}
            WHERE company_id = ${param_num}
            RETURNING id, company_id, embed_api_key, embed_allowed_domains,
                      embed_theme_color, embed_greeting, embed_label,
                      created_at, updated_at
        """

        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *values)
            if row:
                logger.info(f"[CompanySettings] Updated for company {company_id}")
            return dict(row) if row else None

    async def regenerate_api_key(self, company_id: UUID) -> dict | None:
        """Regenerate API key for a company"""
        pool = get_db_pool()
        new_api_key = generate_api_key()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE company_settings
                SET embed_api_key = $2, updated_at = NOW()
                WHERE company_id = $1
                RETURNING id, company_id, embed_api_key, embed_allowed_domains,
                          embed_theme_color, embed_greeting, embed_label,
                          created_at, updated_at
                """,
                company_id,
                new_api_key,
            )
            if row:
                logger.info(f"[CompanySettings] API key regenerated for company {company_id}")
            return dict(row) if row else None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/embed", response_model=EmbedSettings)
async def get_embed_settings(current_company: dict = Depends(get_current_company)):
    """Get embed settings for a company (company from JWT token)"""
    repo = CompanySettingsRepository()
    settings = await repo.get_by_company_id(current_company["id"])

    if not settings:
        # Create default settings if not exists
        settings = await repo.create(current_company["id"])

    return EmbedSettings(
        embed_api_key=settings["embed_api_key"],
        embed_allowed_domains=settings["embed_allowed_domains"] or [],
        embed_theme_color=settings["embed_theme_color"],
        embed_greeting=settings["embed_greeting"],
        embed_label=settings["embed_label"],
    )


@router.put("/embed", response_model=EmbedSettings)
async def update_embed_settings(
    update: EmbedSettingsUpdate,
    current_company: dict = Depends(get_current_company)
):
    """Update embed settings (company from JWT token)"""
    repo = CompanySettingsRepository()

    # Ensure settings exist
    existing = await repo.get_by_company_id(current_company["id"])
    if not existing:
        await repo.create(current_company["id"])

    # Update with provided fields
    update_data = {}
    if update.embed_allowed_domains is not None:
        update_data["embed_allowed_domains"] = update.embed_allowed_domains
    if update.embed_theme_color is not None:
        update_data["embed_theme_color"] = update.embed_theme_color
    if update.embed_greeting is not None:
        update_data["embed_greeting"] = update.embed_greeting
    if update.embed_label is not None:
        update_data["embed_label"] = update.embed_label

    settings = await repo.update(current_company["id"], update_data)

    return EmbedSettings(
        embed_api_key=settings["embed_api_key"],
        embed_allowed_domains=settings["embed_allowed_domains"] or [],
        embed_theme_color=settings["embed_theme_color"],
        embed_greeting=settings["embed_greeting"],
        embed_label=settings["embed_label"],
    )


@router.post("/embed/regenerate-key", response_model=EmbedSettings)
async def regenerate_embed_api_key(current_company: dict = Depends(get_current_company)):
    """Regenerate embed API key (company from JWT token)"""
    repo = CompanySettingsRepository()

    # Ensure settings exist
    existing = await repo.get_by_company_id(current_company["id"])
    if not existing:
        settings = await repo.create(current_company["id"])
    else:
        settings = await repo.regenerate_api_key(current_company["id"])

    return EmbedSettings(
        embed_api_key=settings["embed_api_key"],
        embed_allowed_domains=settings["embed_allowed_domains"] or [],
        embed_theme_color=settings["embed_theme_color"],
        embed_greeting=settings["embed_greeting"],
        embed_label=settings["embed_label"],
    )
