from collections.abc import Callable
from typing import Any, Generic

import jwt
from redis.asyncio import Redis

from loggers import get_logger
from src.core.auth.dependencies import PrincipalT
from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.auth.realm import AuthRealm
from src.core.auth.tokens import create_access_token, rotate_refresh_token
from src.core.errors.exceptions import CoreException
from src.core.schemas import TokenModel
from src.main.config import config

logger = get_logger(__name__)


class RefreshAccessUseCase(Generic[PrincipalT]):
    """
    Rotate a refresh token and mint a new access token for the same session.

    A principal that fails the realm's admission gate is told the real reason,
    unlike login: the caller already proved possession of a valid refresh
    token, so there is nothing left to enumerate. Realm-agnostic code has no
    email to mask, so the rejection is logged against the subject id.

    Rotation detects reuse, and a detected reuse wipes every session of the
    subject rather than only failing this request.
    """

    def __init__(
        self,
        redis_client: Redis,
        realm: AuthRealm,
        claims_builder: Callable[[PrincipalT], dict[str, Any]],
        admission: Callable[[PrincipalT], None],
    ) -> None:
        self.redis_client = redis_client
        self.realm = realm
        self.claims_builder = claims_builder
        self.admission = admission

    async def execute(
        self,
        principal: PrincipalT,
        old_token_payload: JWTPayload,
    ) -> TokenModel:
        try:
            self.admission(principal)
        except CoreException as violation:
            logger.info(
                "[RefreshAccess] Subject '%s' fails admission (%s)",
                old_token_payload["sub"],
                violation,
            )
            raise

        new_refresh_token = await rotate_refresh_token(
            old_token_payload, self.redis_client, realm=self.realm
        )

        new_payload = jwt.decode(
            new_refresh_token,
            self.realm.secret,
            algorithms=[config.jwt.ALGORITHM],
        )

        access_token = await create_access_token(
            self.claims_builder(principal),
            redis_client=self.redis_client,
            session_id=new_payload["session_id"],
            realm=self.realm,
        )

        return TokenModel(
            access_token=access_token,
            refresh_token=new_refresh_token,
        )
