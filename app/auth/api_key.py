"""API key authentication dependency.

Validates an `X-API-Key` header against the api_keys table via the
APIKeyRepository. Designed to be the single shared dependency for
all protected routes so adding rate limiting, scope checks, or
audit logging in a follow-up touches one place.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.rate_limit import RateLimiter, effective_limit, get_rate_limiter
from app.db import SessionLocal
from app.models.api_key import ApiKey
from app.repositories.api_keys import APIKeyRepository


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _rate_limit_headers(
    limit: int, remaining: int, reset: int | None
) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": "null" if reset is None else str(reset),
    }


def api_key_auth(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> ApiKey:
    if x_api_key is None or not x_api_key.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing api key",
        )
    repo = APIKeyRepository(db)
    key = repo.get_by_key(x_api_key)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid api key",
        )
    limit = effective_limit(key.requests_per_minute)
    if limit == 0:
        headers = _rate_limit_headers(0, 0, None)
        request.state.rate_limit_headers = headers
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="api key blocked",
            headers=headers,
        )
    decision = limiter.hit(key.id, limit)
    headers = _rate_limit_headers(decision.limit, decision.remaining, decision.reset)
    request.state.rate_limit_headers = headers
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers=headers,
        )
    return key
