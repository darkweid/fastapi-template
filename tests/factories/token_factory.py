from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt

from src.core.utils.datetime_utils import get_utc_now
from src.main.config import config
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.security import (
    create_access_token,
    create_refresh_token,
    create_reset_password_token,
    create_verification_token,
)


def build_access_payload(
    user_id: str,
    *,
    session_id: str | None = None,
    jti: str | None = None,
    expires_in_minutes: int | None = None,
) -> JWTPayload:
    expire = get_utc_now() + timedelta(
        minutes=expires_in_minutes or config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return {
        "sub": user_id,
        "exp": int(expire.timestamp()),
        "mode": "access_token",
        "jti": jti or str(uuid4()),
        "session_id": session_id or str(uuid4()),
    }


def build_refresh_payload(
    user_id: str,
    *,
    session_id: str | None = None,
    jti: str | None = None,
    family: str | None = None,
    expires_in_minutes: int | None = None,
) -> JWTPayload:
    expire = get_utc_now() + timedelta(
        minutes=expires_in_minutes or config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES
    )
    return {
        "sub": user_id,
        "exp": int(expire.timestamp()),
        "mode": "refresh_token",
        "jti": jti or str(uuid4()),
        "session_id": session_id or str(uuid4()),
        "family": family or str(uuid4()),
    }


def encode_access_payload(payload: JWTPayload) -> str:
    return jwt.encode(payload, config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM)


async def build_access_token(
    data: dict[str, Any],
    redis_client: Any,
    *,
    session_id: str | None = None,
) -> str:
    return await create_access_token(data, redis_client, session_id=session_id)


async def build_refresh_token(
    data: dict[str, Any],
    redis_client: Any,
    *,
    session_id: str | None = None,
    family: str | None = None,
) -> str:
    return await create_refresh_token(
        data, redis_client, session_id=session_id, family=family
    )


def build_verification_token(data: dict[str, Any]) -> str:
    return create_verification_token(data)


def build_reset_password_token(data: dict[str, Any]) -> str:
    return create_reset_password_token(data)
