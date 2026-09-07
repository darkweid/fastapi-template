import pytest

from src.core.auth.session_issuance import issue_session_pair
from src.core.auth.usecases.logout import LogoutUseCase
from src.core.schemas import SuccessResponse
from tests.fakes.redis import InMemoryRedis
from tests.helpers.realm import build_test_realm

REALM = build_test_realm("logout")
SUBJECT_ID = "42"


@pytest.mark.asyncio
async def test_execute_invalidates_only_the_named_session(
    fake_redis: InMemoryRedis,
) -> None:
    await issue_session_pair(
        realm=REALM,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s1",
    )
    await issue_session_pair(
        realm=REALM,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s2",
    )
    use_case = LogoutUseCase(fake_redis, realm=REALM)

    result = await use_case.execute(subject_id=SUBJECT_ID, session_id="s1")

    assert result == SuccessResponse(success=True)
    assert await fake_redis.get(REALM.keys.refresh(SUBJECT_ID, "s1")) is None
    assert await fake_redis.get(REALM.keys.access(SUBJECT_ID, "s1")) is None
    assert await fake_redis.get(REALM.keys.refresh(SUBJECT_ID, "s2")) is not None


@pytest.mark.asyncio
async def test_execute_can_invalidate_every_session_of_the_subject(
    fake_redis: InMemoryRedis,
) -> None:
    await issue_session_pair(
        realm=REALM,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s1",
    )
    await issue_session_pair(
        realm=REALM,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s2",
    )
    use_case = LogoutUseCase(fake_redis, realm=REALM)

    result = await use_case.execute(
        subject_id=SUBJECT_ID, session_id="s1", terminate_all_sessions=True
    )

    assert result == SuccessResponse(success=True)
    assert await fake_redis.get(REALM.keys.refresh(SUBJECT_ID, "s1")) is None
    assert await fake_redis.get(REALM.keys.refresh(SUBJECT_ID, "s2")) is None


@pytest.mark.asyncio
async def test_execute_uses_the_realms_own_key_namespace(
    fake_redis: InMemoryRedis,
) -> None:
    other_realm = build_test_realm("logout-other")
    await issue_session_pair(
        realm=REALM,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s1",
    )
    await issue_session_pair(
        realm=other_realm,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s1",
    )
    use_case = LogoutUseCase(fake_redis, realm=REALM)

    await use_case.execute(
        subject_id=SUBJECT_ID, session_id="s1", terminate_all_sessions=True
    )

    assert await fake_redis.get(REALM.keys.refresh(SUBJECT_ID, "s1")) is None
    assert await fake_redis.get(other_realm.keys.refresh(SUBJECT_ID, "s1")) is not None
