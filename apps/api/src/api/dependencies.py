import hashlib
import hmac as hmac_mod
import os
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.database import CompanyRepository
from core.utils.auth import decode_access_token

security = HTTPBearer(auto_error=False)

HMAC_TIME_WINDOW = 300  # ±5 minutes
_used_nonces: dict[str, float] = {}  # {nonce: timestamp}


def _cleanup_nonces() -> None:
    now = time.time()
    expired = [n for n, ts in _used_nonces.items() if now - ts > HMAC_TIME_WINDOW]
    for n in expired:
        del _used_nonces[n]


async def verify_hmac_signature(
    request: Request,
    x_timestamp: Annotated[str, Header()],
    x_nonce: Annotated[str, Header()],
    x_signature: Annotated[str, Header()],
) -> None:
    """
    Verify HMAC-SHA256 signature for external service authentication.

    Headers:
        X-Timestamp: Unix timestamp (seconds)
        X-Nonce: UUID or random 16-byte hex
        X-Signature: HMAC-SHA256(secret, "METHOD\\nPATH\\nTIMESTAMP\\nNONCE") → hex
    """
    secret = os.getenv("INBOUND_SIGNING_KEY")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INBOUND_SIGNING_KEY not configured",
        )

    try:
        ts = int(x_timestamp)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid timestamp",
        )

    if abs(time.time() - ts) > HMAC_TIME_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Request timestamp expired",
        )

    canonical = f"{request.method}\n{request.url.path}\n{x_timestamp}\n{x_nonce}"
    expected = hmac_mod.new(
        secret.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac_mod.compare_digest(expected, x_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    _cleanup_nonces()

    if x_nonce in _used_nonces:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nonce already used",
        )

    _used_nonces[x_nonce] = time.time()


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


async def get_optional_company(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]
) -> dict | None:
    """
    Optional authentication - returns company if token valid, None otherwise.
    Useful for endpoints that work both authenticated and unauthenticated.
    """
    if credentials is None:
        return None

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        return None

    company_name = payload.get("sub")
    if not company_name:
        return None

    company_repo = CompanyRepository()
    return await company_repo.get_by_name(company_name)
