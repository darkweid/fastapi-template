from dataclasses import dataclass
from unittest.mock import Mock

import jwt
import pytest

from src.core.auth.session_issuance import issue_session_pair
from src.core.auth.usecases.refresh_access import RefreshAccessUseCase
from src.core.errors.exceptions import AccessForbiddenException
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
        claims_builder=lambda p: {"sub": p.id},
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
async def test_execute_builds_access_claims_from_the_principal(
    fake_redis: InMemoryRedis,
) -> None:
    # A different subject id than the one the refresh token names proves the
    # access token's "sub" really comes from claims_builder(principal), not
    # from the old payload it was handed alongside.
    old_payload = await _build_old_payload(fake_redis, session_id="s2")
    principal = FakePrincipal(id="99")
    claims_builder = Mock(return_value={"sub": "99"})
    use_case = RefreshAccessUseCase(
        fake_redis,
        realm=REALM,
        claims_builder=claims_builder,
        admission=Mock(),
    )

    result = await use_case.execute(principal, old_payload)

    claims_builder.assert_called_once_with(principal)
    new_access_payload = jwt.decode(
        result.access_token, REALM.secret, algorithms=[config.jwt.ALGORITHM]
    )
    assert new_access_payload["sub"] == "99"


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
        claims_builder=lambda p: {"sub": p.id},
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
        claims_builder=lambda p: {"sub": p.id},
        admission=admission,
    )

    with pytest.raises(AccessForbiddenException):
        await use_case.execute(principal, old_payload)

    # A denied admission must never rotate the refresh token: the old one
    # must still be the live one for this session.
    assert await fake_redis.get(REALM.keys.refresh(SUBJECT_ID, "s4")) is not None
