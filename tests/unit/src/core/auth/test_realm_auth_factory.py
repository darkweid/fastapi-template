from dataclasses import dataclass

import pytest

from src.core.auth.dependencies import build_realm_auth
from src.core.auth.realm import AuthRealm
from src.core.auth.tokens import create_access_token
from src.core.errors.exceptions import UnauthorizedException


@dataclass
class FakePrincipal:
    id: str
    is_active: bool = True


class FakePrincipalRepository:
    def __init__(self, principal: FakePrincipal | None = None) -> None:
        self._principal = principal

    async def get_single(
        self, session: object, **filters: object
    ) -> FakePrincipal | None:
        return self._principal


FIRST_REALM = AuthRealm(
    name="first",
    session_secret=lambda: "first-realm-secret-not-real-32-chars",
    refresh_cookie_path="/v1/first/auth/login/refresh",
)
SECOND_REALM = AuthRealm(
    name="second",
    session_secret=lambda: "second-realm-secret-not-real-32-char",
    refresh_cookie_path="/v1/second/auth/login/refresh",
)


async def test_a_token_minted_for_one_realm_is_rejected_by_another(
    fake_redis: object,
) -> None:
    principal = FakePrincipal(id="42")
    second_auth = build_realm_auth(
        realm=SECOND_REALM,
        principal_type=FakePrincipal,
        repository_factory=lambda: FakePrincipalRepository(principal),
        admission=lambda _principal: None,
    )
    token = await create_access_token(
        {"sub": "42"}, fake_redis, realm=FIRST_REALM, session_id="s1"
    )

    with pytest.raises(UnauthorizedException):
        await second_auth.authenticate(
            token=token, session=None, redis_client=fake_redis
        )
