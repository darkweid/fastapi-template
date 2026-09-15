"""Event log behaviour that only real SQL can prove.

The SAVEPOINT that keeps a rejected audit row from taking the caller's work with
it, the INET round-trip and the index that answers the default page without a
sort are all properties of the database, not of the Python around it.
"""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.query import ListQuery
from src.core.pagination import PaginationParams, make_paginated_response
from src.core.utils.datetime_utils import get_utc_now
from src.core.utils.security import password_hasher
from src.event_log.actor import Actor
from src.event_log.enums import ActorType, ObjectType
from src.event_log.events import DomainEvent
from src.event_log.models import EventLog
from src.event_log.repositories import EventLogRepository
from src.event_log.schemas import EventLogViewModel
from src.user.enums import UserRole
from src.user.models import User
from src.user.repositories import UserRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Argon2 hashing costs ~100ms per call by design, and the model's validator wants
# a real hash. One hash for the whole module is enough.
PASSWORD_HASH = password_hasher.hash("integration-password")


class RejectedEvent(DomainEvent):
    """A code one character past `event_logs.event_type`, so the INSERT fails."""

    code = "x" * 65
    object_type = ObjectType.USER


class InetEvent(DomainEvent):
    code = "test.inet_serialization"
    object_type = ObjectType.USER


def _contains_sort_node(plan: dict[str, Any]) -> bool:
    if str(plan.get("Node Type", "")).endswith("Sort"):
        return True
    return any(_contains_sort_node(child) for child in plan.get("Plans", []))


async def test_rejected_event_insert_leaves_the_action_committed(
    db_session: AsyncSession,
) -> None:
    """The whole point of the SAVEPOINT: a broken audit row must not roll back
    the action it describes."""
    username = f"savepoint_{uuid4().hex[:8]}"
    user = await UserRepository().create(
        db_session,
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": f"{username}@example.com",
            "username": username,
            "phone_number": "+10000000000",
            "password_hash": PASSWORD_HASH,
            "role": UserRole.VIEWER,
            "is_verified": True,
            "is_active": True,
        },
    )
    await EventLogRepository().record(
        db_session, Actor.user(user.id), RejectedEvent(object_id=user.id)
    )
    await db_session.commit()

    try:
        persisted_user = await UserRepository().get_single(db_session, id=user.id)
        rejected_log = await EventLogRepository().get_single(
            db_session, event_type=RejectedEvent.code
        )

        assert persisted_user is not None
        assert rejected_log is None
    finally:
        await db_session.execute(delete(User).where(User.id == user.id))
        await db_session.commit()


async def test_a_stored_address_survives_response_serialization(
    db_session: AsyncSession,
) -> None:
    """`ip_address` is INET in the database and `IPvAnyAddress` in the schema;
    a v6 address is where a str-typed column would have got away with it."""
    object_id = uuid4()
    await EventLogRepository().record(
        db_session, Actor.anonymous(ip="2001:db8::42"), InetEvent(object_id=object_id)
    )
    await db_session.flush()
    db_session.expunge_all()

    items, total = await EventLogRepository().get_paginated_list(
        db_session,
        page=1,
        size=50,
        actor_type=ActorType.ANONYMOUS,
        object_id=object_id,
        event_type=InetEvent.code,
    )
    response = make_paginated_response(
        items=items,
        total=total,
        pagination=PaginationParams(page=1, size=50),
        schema=EventLogViewModel,
    )

    assert total == 1
    assert response.model_dump(mode="json")["items"][0]["ip_address"] == "2001:db8::42"


async def test_page_query_needs_no_sort_node(db_session: AsyncSession) -> None:
    """`ix_event_logs_created_at` carries the order `ListQuery` asks for; without
    that, reading the newest fifty rows sorts the whole table first."""
    marker = f"test.plan_{uuid4().hex[:8]}"
    started_at = get_utc_now() - timedelta(days=1)
    await db_session.execute(
        insert(EventLog),
        [
            {
                "id": uuid4(),
                "actor_type": ActorType.SYSTEM,
                "actor_id": None,
                "object_type": None,
                "object_id": None,
                "event_type": marker,
                "payload": {},
                "ip_address": None,
                "created_at": started_at + timedelta(microseconds=index),
            }
            for index in range(1000)
        ],
    )
    repository = EventLogRepository()
    list_query = ListQuery()
    statement = (
        select(EventLog)
        .where(*list_query.build_where_clauses(EventLog, repository.searchable_fields))
        .order_by(
            *list_query.build_order_by(
                EventLog,
                repository.sortable_fields,
                repository.default_order_by,
            )
        )
        .offset(0)
        .limit(50)
    )
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    await db_session.execute(text("SET LOCAL enable_sort = off"))
    explained = await db_session.scalar(text(f"EXPLAIN (FORMAT JSON) {compiled}"))
    plan = explained[0]["Plan"]

    assert not _contains_sort_node(plan)
