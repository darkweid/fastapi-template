from fastapi import Response
import jwt
import pytest

from src.core.auth.cookies import TokenCookieResponder
from src.core.auth.session_issuance import issue_session_pair
from src.core.auth.token_helpers import invalidate_all_sessions
from src.core.auth.token_transport import TokenTransport
from src.core.auth.tokens import rotate_refresh_token
from src.core.errors.exceptions import UnauthorizedException
from src.core.schemas import TokenModel
from src.main.config import config
from tests.helpers.realm import build_test_realm

FIRST = build_test_realm("first")
SECOND = build_test_realm("second")
SUBJECT_ID = "42"


async def test_a_session_wipe_leaves_the_other_realm_untouched(
    fake_redis: object,
) -> None:
    await issue_session_pair(
        realm=FIRST,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s1",
    )
    await issue_session_pair(
        realm=SECOND,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s2",
    )

    await invalidate_all_sessions(SUBJECT_ID, fake_redis, keys=FIRST.keys)

    assert await fake_redis.get(FIRST.keys.refresh(SUBJECT_ID, "s1")) is None
    assert await fake_redis.get(SECOND.keys.refresh(SUBJECT_ID, "s2")) is not None


async def test_two_realms_write_distinct_cookies() -> None:
    response = Response()
    for realm in (FIRST, SECOND):
        TokenCookieResponder(
            realm=realm,
            cookie_config=config.cookie,
            refresh_token_expire_minutes=60,
        ).apply(
            TokenModel(access_token="a", refresh_token=f"r-{realm.name}"),
            response,
            TokenTransport.COOKIE,
        )

    written = response.headers.getlist("set-cookie")
    names = {header.split("=", 1)[0] for header in written}
    assert names == {
        "first_refresh_token",
        "first_csrf_token",
        "second_refresh_token",
        "second_csrf_token",
    }


async def test_a_refresh_token_of_one_realm_cannot_rotate_in_another(
    fake_redis: object,
) -> None:
    tokens = await issue_session_pair(
        realm=FIRST,
        subject_id=SUBJECT_ID,
        claims={},
        redis_client=fake_redis,
        session_id="s1",
    )
    payload = jwt.decode(
        tokens.refresh_token, FIRST.secret, algorithms=[config.jwt.ALGORITHM]
    )

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis, realm=SECOND)

    # A foreign signature must never trigger the protective family wipe: that
    # would let one realm log a subject out of every session in another.
    assert await fake_redis.get(FIRST.keys.refresh(SUBJECT_ID, "s1")) is not None
