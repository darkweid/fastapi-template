import traceback
from typing import Any
from unittest.mock import AsyncMock

from aiosmtplib import SMTPRecipientRefused, SMTPRecipientsRefused
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.core.errors.scrubbed import ScrubbedError
from src.user.auth import tasks as auth_tasks
from src.user.auth.realm import (
    RESET_PASSWORD_PURPOSE,
    USER_AUTH_REALM,
    VERIFICATION_PURPOSE,
)
from src.user.auth.tasks import (
    send_reset_password_email_task,
    send_verification_email_task,
)
from tests.fakes.email import MockMailer
from tests.fakes.redis import InMemoryRedis
from tests.helpers.logs import capture_logger
from tests.helpers.providers import ProvideValue


@pytest.mark.asyncio
async def test_send_verification_email_creates_token_and_sends_email(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))

    await send_verification_email_task(
        "user@example.com",
        "John Doe",
        throttle_key=None,
        retry=False,
        redis_client=fake_redis,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    sent_email = mock_mailer.sent_template_emails[0]
    assert sent_email["recipients"] == ["user@example.com"]
    assert sent_email["template_name"] == "verification.html"
    assert sent_email["template_data"]["link"].startswith(
        "http://frontend.test/verify-email?token="
    )
    assert (
        await fake_redis.exists(
            USER_AUTH_REALM.keys.one_time(VERIFICATION_PURPOSE, "user@example.com")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_send_verification_email_cleans_up_token_and_throttle_on_failure(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr(
        mock_mailer,
        "send_template",
        AsyncMock(side_effect=RuntimeError("send failed")),
    )
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))
    await fake_redis.set("throttle:key", "1", ex=60)

    with pytest.raises(ScrubbedError, match="^RuntimeError$"):
        await send_verification_email_task(
            "user@example.com",
            "John Doe",
            throttle_key="throttle:key",
            throttle_owner="1",
            retry=False,
            redis_client=fake_redis,
        )

    assert (
        await fake_redis.exists(
            USER_AUTH_REALM.keys.one_time(VERIFICATION_PURPOSE, "user@example.com")
        )
        == 0
    )
    assert await fake_redis.exists("throttle:key") == 0


@pytest.mark.asyncio
async def test_send_reset_password_email_creates_token_and_sends_email(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))

    await send_reset_password_email_task(
        "user@example.com",
        "John Doe",
        throttle_key=None,
        retry=False,
        redis_client=fake_redis,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    sent_email = mock_mailer.sent_template_emails[0]
    assert sent_email["recipients"] == ["user@example.com"]
    assert sent_email["template_name"] == "reset_password.html"
    assert sent_email["template_data"]["link"].startswith(
        "http://frontend.test/reset-password?token="
    )
    assert (
        await fake_redis.exists(
            USER_AUTH_REALM.keys.one_time(RESET_PASSWORD_PURPOSE, "user@example.com")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_send_reset_password_email_cleans_up_token_and_throttle_on_failure(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr(
        mock_mailer,
        "send_template",
        AsyncMock(side_effect=RuntimeError("send failed")),
    )
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))
    await fake_redis.set("throttle:key", "1", ex=60)

    with pytest.raises(ScrubbedError, match="^RuntimeError$"):
        await send_reset_password_email_task(
            "user@example.com",
            "John Doe",
            throttle_key="throttle:key",
            throttle_owner="1",
            retry=False,
            redis_client=fake_redis,
        )

    assert (
        await fake_redis.exists(
            USER_AUTH_REALM.keys.one_time(RESET_PASSWORD_PURPOSE, "user@example.com")
        )
        == 0
    )
    assert await fake_redis.exists("throttle:key") == 0


@pytest.mark.asyncio
async def test_send_verification_email_releases_throttle_when_token_creation_fails(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token issuance failing (e.g. transient Redis error) must release the
    throttle key exactly like a send failure, or resends stay locked out."""
    mock_mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))
    monkeypatch.setattr(
        "src.user.auth.tasks.issue_one_time_token",
        AsyncMock(side_effect=RuntimeError("token issuance failed")),
    )
    await fake_redis.set("throttle:key", "1", ex=60)

    with pytest.raises(ScrubbedError, match="^RuntimeError$"):
        await send_verification_email_task(
            "user@example.com",
            "John Doe",
            throttle_key="throttle:key",
            throttle_owner="1",
            retry=False,
            redis_client=fake_redis,
        )

    assert await fake_redis.exists("throttle:key") == 0
    assert len(mock_mailer.sent_template_emails) == 0


def test_auth_task_registration() -> None:
    assert send_verification_email_task.task_name == "send_verification_email"
    assert send_verification_email_task.labels["retry_on_error"] is True
    assert send_reset_password_email_task.task_name == "send_reset_password_email"
    assert send_reset_password_email_task.labels["retry_on_error"] is True


TASKS = [
    pytest.param(send_verification_email_task, VERIFICATION_PURPOSE, id="verification"),
    pytest.param(
        send_reset_password_email_task, RESET_PASSWORD_PURPOSE, id="reset-password"
    ),
]
EMAIL = "user@example.com"


@pytest.mark.parametrize(("task", "purpose"), TASKS)
async def test_a_retry_leaves_the_link_of_a_newer_send_working(
    task: Any,
    purpose: str,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failed run released the throttle, so the user asked again and holds a
    working link; the retry must not replace it."""
    mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mailer))
    key = USER_AUTH_REALM.keys.one_time(purpose, EMAIL)
    await fake_redis.set(key, "newer-jti", ex=600)
    await fake_redis.set("throttle:key", "newer-owner", ex=60)

    await task(
        EMAIL,
        "John Doe",
        throttle_key="throttle:key",
        throttle_owner="older-owner",
        retry=True,
        redis_client=fake_redis,
    )

    assert mailer.sent_template_emails == []
    assert await fake_redis.get(key) == "newer-jti"
    assert await fake_redis.get("throttle:key") == "newer-owner"


@pytest.mark.parametrize(("task", "purpose"), TASKS)
async def test_a_retry_with_no_live_challenge_sends(
    task: Any,
    purpose: str,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mailer))

    await task(EMAIL, "John Doe", retry=True, redis_client=fake_redis)

    assert len(mailer.sent_template_emails) == 1
    key = USER_AUTH_REALM.keys.one_time(purpose, EMAIL)
    assert await fake_redis.exists(key) == 1


@pytest.mark.parametrize(("task", "purpose"), TASKS)
async def test_a_first_run_replaces_the_previous_link(
    task: Any,
    purpose: str,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mailer))
    key = USER_AUTH_REALM.keys.one_time(purpose, EMAIL)
    await fake_redis.set(key, "older-jti", ex=600)

    await task(EMAIL, "John Doe", retry=False, redis_client=fake_redis)

    assert len(mailer.sent_template_emails) == 1
    assert await fake_redis.get(key) != "older-jti"


@pytest.mark.parametrize(("task", "purpose"), TASKS)
async def test_a_failed_send_takes_back_only_what_it_holds(
    task: Any,
    purpose: str,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A send can fail after its slot expired and a newer send took over both
    the slot and the challenge."""
    key = USER_AUTH_REALM.keys.one_time(purpose, EMAIL)
    await fake_redis.set("throttle:key", "owner", ex=60)

    async def superseded(*_: object, **__: object) -> None:
        await fake_redis.set(key, "newer-jti", ex=600)
        await fake_redis.set("throttle:key", "newer-owner", ex=60)
        raise RuntimeError("send failed")

    mailer = MockMailer()
    monkeypatch.setattr(mailer, "send_template", superseded)
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mailer))

    with pytest.raises(ScrubbedError, match="^RuntimeError$"):
        await task(
            EMAIL,
            "John Doe",
            throttle_key="throttle:key",
            throttle_owner="owner",
            retry=False,
            redis_client=fake_redis,
        )

    assert await fake_redis.get(key) == "newer-jti"
    assert await fake_redis.get("throttle:key") == "newer-owner"


@pytest.mark.parametrize(("task", "purpose"), TASKS)
@pytest.mark.parametrize("retry", [False, True])
async def test_a_challenge_stored_with_a_lost_reply_is_taken_back(
    task: Any,
    purpose: str,
    retry: bool,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry would take it for the link of a newer send and mail nothing."""
    mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mailer))
    store = fake_redis.set

    async def lose_reply(*args: Any, **kwargs: Any) -> bool:
        await store(*args, **kwargs)
        raise RedisConnectionError("reply lost")

    with monkeypatch.context() as lossy:
        lossy.setattr(fake_redis, "set", lose_reply)
        with pytest.raises(ScrubbedError, match="^ConnectionError$"):
            await task(EMAIL, "John Doe", retry=retry, redis_client=fake_redis)
    key = USER_AUTH_REALM.keys.one_time(purpose, EMAIL)
    assert await fake_redis.exists(key) == 0

    await task(EMAIL, "John Doe", retry=True, redis_client=fake_redis)

    assert len(mailer.sent_template_emails) == 1


async def test_a_failed_store_takes_back_no_challenge_but_its_own(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = USER_AUTH_REALM.keys.one_time(VERIFICATION_PURPOSE, EMAIL)
    await fake_redis.set(key, "newer-jti", ex=600)
    monkeypatch.setattr(
        fake_redis, "set", AsyncMock(side_effect=RedisConnectionError("down"))
    )

    with pytest.raises(ScrubbedError, match="^ConnectionError$"):
        await send_verification_email_task(
            EMAIL, "John Doe", retry=True, redis_client=fake_redis
        )

    assert await fake_redis.get(key) == "newer-jti"


async def test_a_refused_recipient_stays_out_of_the_log_and_the_raised_error(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mailer = MockMailer()
    refused = SMTPRecipientsRefused([SMTPRecipientRefused(550, "unknown", EMAIL)])
    monkeypatch.setattr(mailer, "send_template", AsyncMock(side_effect=refused))
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mailer))

    with (
        capture_logger(caplog, auth_tasks.logger),
        pytest.raises(ScrubbedError) as caught,
    ):
        await send_verification_email_task(
            EMAIL, "John Doe", retry=False, redis_client=fake_redis
        )

    for text in (caplog.text, "".join(traceback.format_exception(caught.value))):
        assert "SMTPRecipientsRefused" in text
        assert EMAIL not in text
