"""Authentication API endpoints for company login/signup."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from core.database import CompanyRepository
from core.utils import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    get_password_hash,
    verify_password,
)

from ..dependencies import get_current_company

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# =============================================================================
# Pydantic Models
# =============================================================================

class SignupRequest(BaseModel):
    name: str
    password: str
    confirm_password: str

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) < 3:
            raise ValueError('Name must be at least 3 characters')
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Name can only contain letters, numbers, underscores, and hyphens')
        return v

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError('Password must be at least 6 characters')
        return v


class LoginRequest(BaseModel):
    name: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token expiry in seconds
    company: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class CompanyResponse(BaseModel):
    id: str
    name: str
    display_name: str | None


# =============================================================================
# Auth Endpoints
# =============================================================================

@router.post("/signup", response_model=TokenResponse)
async def signup(request: SignupRequest):
    """
    Register a new company account.

    - Validates name uniqueness
    - Hashes password with bcrypt
    - Returns JWT token on success
    """
    if request.password != request.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match"
        )

    company_repo = CompanyRepository()

    # Check if company already exists
    existing = await company_repo.get_by_name(request.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company name already registered"
        )

    # Create company with password hash
    password_hash = get_password_hash(request.password)
    company = await company_repo.create_with_password(request.name, password_hash)

    logger.info(f"[Signup] New company registered: {request.name}")

    # Generate JWT tokens
    token_data = {"sub": company["name"]}
    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token = create_refresh_token(data=token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        company={
            "id": str(company["id"]),
            "name": company["name"],
            "display_name": company.get("display_name"),
        }
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Login with company name and password.

    - Verifies password against stored hash
    - Returns JWT token on success
    """
    company_repo = CompanyRepository()

    # Get company by name
    company = await company_repo.get_by_name(request.name.strip().lower())
    if not company:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    # Check if company has a password set
    if not company.get("password_hash"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This company does not have a password set. Please contact support."
        )

    # Verify password
    if not verify_password(request.password, company["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    logger.info(f"[Login] Company logged in: {company['name']}")

    # Generate JWT tokens
    token_data = {"sub": company["name"]}
    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token = create_refresh_token(data=token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        company={
            "id": str(company["id"]),
            "name": company["name"],
            "display_name": company.get("display_name"),
        }
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest):
    """
    Refresh access token using refresh token.

    - Validates refresh token
    - Issues new access token
    - Returns existing refresh token (unchanged)
    """
    payload = decode_refresh_token(request.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )

    company_name = payload.get("sub")
    if not company_name:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token payload"
        )

    company_repo = CompanyRepository()
    company = await company_repo.get_by_name(company_name)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Company not found"
        )

    logger.info(f"[Refresh] Token refreshed for: {company_name}")

    # Generate new access token (keep existing refresh token)
    new_access_token = create_access_token(data={"sub": company_name})

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=request.refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        company={
            "id": str(company["id"]),
            "name": company["name"],
            "display_name": company.get("display_name"),
        }
    )


@router.get("/me", response_model=CompanyResponse)
async def get_me(current_company: dict = Depends(get_current_company)):
    """
    Get current authenticated company info.

    Requires valid JWT token in Authorization header.
    """
    return CompanyResponse(
        id=str(current_company["id"]),
        name=current_company["name"],
        display_name=current_company.get("display_name"),
    )
