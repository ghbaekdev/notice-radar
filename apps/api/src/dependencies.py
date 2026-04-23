from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.database import CompanyRepository
from core.utils.auth import decode_access_token
from core.utils.env import load_app_env

load_app_env("api")

security = HTTPBearer(auto_error=False)


async def get_current_company(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]
) -> dict:
    """
    Dependency to get the current authenticated company from JWT token.
    Returns the company dict if authenticated, raises 401 otherwise.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    company_name = payload.get("sub")
    if not company_name:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # Get company from database
    company_repo = CompanyRepository()
    company = await company_repo.get_by_name(company_name)

    if not company:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Company not found",
        )

    return company
