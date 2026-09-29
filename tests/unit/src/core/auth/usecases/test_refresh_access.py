from dataclasses import dataclass
from unittest.mock import Mock

import jwt
import pytest

from src.core.auth.credentials import verify_jti
from src.core.auth.session_issuance import issue_session_pair
from src.core.auth.usecases.refresh_access import RefreshAccessUseCase
from src.core.errors.exceptions import AccessForbiddenException, UnauthorizedException
from src.main.config import config
from tests.fakes.redis import InMemoryRedis
from tests.helpers.realm import build_test_realm

REALM = build_test_realm("refresh-access")
SUBJECT_ID = "42"


@dataclass
class FakePrincipal:
    id: str


async def _build_old_payload(fake_redis: InMemoryRedis, session_id: str = "s1"):
    tokens = await issue_session_pair(
        realm=REALM,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id=session_id,
    )
    return jwt.decode(
        tokens.refresh_token, REALM.secret, algorithms=[config.jwt.ALGORITHM]
    )


@pytest.mark.asyncio
async def test_execute_rotates_the_refresh_token_and_keeps_the_session_id(
    fake_redis: InMemoryRedis,
) -> None:
    old_payload = await _build_old_payload(fake_redis, session_id="s1")
    principal = FakePrincipal(id=SUBJECT_ID)
    use_case = RefreshAccessUseCase(
        fake_redis,
        realm=REALM,
        admission=Mock(),
    )

    result = await use_case.execute(principal, old_payload)

    new_refresh_payload = jwt.decode(
        result.refresh_token, REALM.secret, algorithms=[config.jwt.ALGORITHM]
    )
    new_access_payload = jwt.decode(
        result.access_token, REALM.secret, algorithms=[config.jwt.ALGORITHM]
    )
    assert new_refresh_payload["session_id"] == "s1"
    assert new_access_payload["session_id"] == "s1"
    assert new_access_payload["mode"] == "access_token"


@pytest.mark.asyncio
async def test_execute_hands_out_a_live_pair_and_retires_the_old_access_token(
    fake_redis: InMemoryRedis,
) -> None:
    """The use case no longer registers the access token itself, so the pair the
    rotation script stored must be the one that verifies."""
    old_tokens = await issue_session_pair(
        realm=REALM,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s2",
    )
    old_payload = jwt.decode(
        old_tokens.refresh_token, REALM.secret, algorithms=[config.jwt.ALGORITHM]
    )
    use_case = RefreshAccessUseCase(fake_redis, realm=REALM, admission=Mock())

    result = await use_case.execute(FakePrincipal(id=SUBJECT_ID), old_payload)

    access_payload = await verify_jti(result.access_token, fake_redis, realm=REALM)
    refresh_payload = await verify_jti(result.refresh_token, fake_redis, realm=REALM)
    assert access_payload["sub"] == SUBJECT_ID
    assert refresh_payload["sub"] == SUBJECT_ID
    with pytest.raises(UnauthorizedException):
        await verify_jti(old_tokens.access_token, fake_redis, realm=REALM)


@pytest.mark.asyncio
async def test_execute_runs_the_admission_gate_against_the_principal(
    fake_redis: InMemoryRedis,
) -> None:
    old_payload = await _build_old_payload(fake_redis, session_id="s3")
    principal = FakePrincipal(id=SUBJECT_ID)
    admission = Mock()
    use_case = RefreshAccessUseCase(
        fake_redis,
        realm=REALM,
        admission=admission,
    )

    await use_case.execute(principal, old_payload)

    admission.assert_called_once_with(principal)


@pytest.mark.asyncio
async def test_execute_propagates_whatever_admission_raises_without_rotating(
    fake_redis: InMemoryRedis,
) -> None:
    old_payload = await _build_old_payload(fake_redis, session_id="s4")
    principal = FakePrincipal(id=SUBJECT_ID)

    def admission(_: FakePrincipal) -> None:
        raise AccessForbiddenException("blocked")

    use_case = RefreshAccessUseCase(
        fake_redis,
        realm=REALM,
        admission=admission,
    )

    with pytest.raises(AccessForbiddenException):
        await use_case.execute(principal, old_payload)

    # A denied admission must never rotate the refresh token: the old one
    # must still be the live one for this session.
    assert await fake_redis.get(REALM.keys.refresh(SUBJECT_ID, "s4")) is not None
