from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    OperationalError,
    SQLAlchemyError,
    TimeoutError as PoolTimeoutError,
)

from src.event_log.actor import Actor
from src.event_log.enums import ActorType, ObjectType
from src.event_log.events import DomainEvent, PublishedEvent
from src.event_log.repositories import EventLogRepository, logger
from tests.helpers.logs import capture_logger


class SomethingHappened(DomainEvent):
    # Deliberately not a `note.*` or `user.*` code: the catalog test asserts
    # that a module's codes match its package, and this class lives in tests.
    code = "test.something_happened"
    object_type = ObjectType.NOTE

    changes: dict[str, list[Any]]
    signed_up_on: date


OCCURRED_AT = datetime(2020, 1, 1, tzinfo=UTC)


async def _record(session: Any, actor: Actor, event: DomainEvent) -> PublishedEvent:
    published = PublishedEvent(
        id=uuid4(), actor=actor, event=event, occurred_at=OCCURRED_AT
    )
    await EventLogRepository().record(session, published)
    return published


async def test_actor_and_event_land_in_their_own_columns(fake_session) -> None:
    """The columns are what the log is queried by; the payload is not indexed."""
    user_id = uuid4()

    published = await _record(
        fake_session,
        Actor.user(user_id, ip="10.0.0.1"),
        SomethingHappened(
            object_id=user_id,
            changes={"title": [None, "Draft"]},
            signed_up_on=date(2020, 1, 1),
        ),
    )

    row = fake_session.added[0]
    assert row.actor_type is ActorType.USER
    assert row.actor_id == user_id
    assert row.object_type is ObjectType.NOTE
    assert row.object_id == user_id
    assert row.event_type == "test.something_happened"
    assert row.ip_address == "10.0.0.1"
    # One id and one instant for the row and every subscriber of the event.
    assert (row.id, row.created_at) == (published.id, OCCURRED_AT)


async def test_payload_is_json_safe_and_holds_no_column_twice(fake_session) -> None:
    """`object_id` is a column; repeating it in JSONB invites the two to drift."""
    note_id = uuid4()

    await _record(
        fake_session,
        Actor.user(uuid4()),
        SomethingHappened(
            object_id=note_id,
            changes={"title": [None, "Draft"]},
            signed_up_on=date(2020, 1, 1),
        ),
    )

    payload = fake_session.added[0].payload
    assert "object_id" not in payload
    # `mode="json"` is what keeps UUID / date out of the JSONB serializer's way.
    assert payload["signed_up_on"] == "2020-01-01"


async def test_a_failed_insert_leaves_the_action_alone(fake_session) -> None:
    """The log must never be the reason a sign-in or a write fails."""
    fake_session.fail_nested_with = IntegrityError("insert", {}, Exception("boom"))

    with patch("src.event_log.repositories.sentry_sdk.capture_exception") as captured:
        await _record(
            fake_session,
            Actor.system(),
            SomethingHappened(
                object_id=uuid4(), changes={}, signed_up_on=date(2020, 1, 1)
            ),
        )

    assert captured.call_count == 1


async def test_a_failing_action_is_not_reported_as_a_logging_failure(
    fake_session,
) -> None:
    """`begin_nested()` flushes; without the explicit flush first, the caller's
    own IntegrityError would surface from inside `record` and be swallowed."""
    fake_session.flush = AsyncMock(
        side_effect=IntegrityError("insert", {}, Exception("duplicate key"))
    )

    with patch("src.event_log.repositories.sentry_sdk.capture_exception") as captured:
        with pytest.raises(IntegrityError):
            await _record(
                fake_session,
                Actor.system(),
                SomethingHappened(
                    object_id=uuid4(), changes={}, signed_up_on=date(2020, 1, 1)
                ),
            )

    assert captured.call_count == 0


async def test_a_failed_insert_is_reported_without_the_row(
    fake_session, caplog: pytest.LogCaptureFixture
) -> None:
    """The DETAIL line echoes the payload and the caller's address."""
    fake_session.fail_nested_with = IntegrityError(
        "insert", {}, Exception("Failing row contains (10.0.0.1, secret@example.com)")
    )

    with (
        patch("src.event_log.repositories.sentry_sdk.capture_exception") as captured,
        capture_logger(caplog, logger),
    ):
        await _record(
            fake_session,
            Actor.system(),
            SomethingHappened(
                object_id=uuid4(), changes={}, signed_up_on=date(2020, 1, 1)
            ),
        )

    assert "Failed to record test.something_happened" in caplog.text
    assert "secret@example.com" not in caplog.text
    [reported] = captured.call_args.args
    assert "secret@example.com" not in str(reported)
    assert reported.__cause__ is None


class DriverError(Exception):
    sqlstate = "53300"


class DatabaseDropped(Exception):
    sqlstate = "57P04"


@pytest.mark.parametrize(
    "outage",
    [
        DBAPIError("insert", {}, Exception("closed"), connection_invalidated=True),
        DBAPIError("insert", {}, DriverError()),
        DBAPIError("insert", {}, DatabaseDropped(), connection_invalidated=True),
        OperationalError("insert", {}, Exception("refused")),
        PoolTimeoutError("QueuePool limit"),
    ],
    ids=["invalidated", "sqlstate", "dropped", "operational", "pool"],
)
async def test_an_unreachable_database_is_the_callers_error(
    fake_session, outage: SQLAlchemyError
) -> None:
    """Its transaction is gone, so a swallowed failure resurfaces at the commit
    as a PendingRollbackError and turns a 503 into a 500."""
    fake_session.fail_nested_with = outage

    with (
        patch("src.event_log.repositories.sentry_sdk.capture_exception") as captured,
        pytest.raises(type(outage)),
    ):
        await _record(
            fake_session,
            Actor.system(),
            SomethingHappened(
                object_id=uuid4(), changes={}, signed_up_on=date(2020, 1, 1)
            ),
        )

    captured.assert_not_called()
