from uuid import uuid4

from src.event_log.actor import Actor
from src.event_log.enums import ActorType


def test_named_constructors_cannot_confuse_type_and_id() -> None:
    """The constructor is the only place that pairs a type with an id."""
    user_id = uuid4()

    assert Actor.user(user_id, ip="10.0.0.1") == Actor(
        actor_type=ActorType.USER, actor_id=user_id, ip="10.0.0.1"
    )


def test_the_actor_keeps_the_enum_member_not_its_value() -> None:
    """`Base` turns enums into strings; the log's own types must survive that,
    or every `is` comparison downstream silently compares against a str."""
    assert Actor.user(uuid4()).actor_type is ActorType.USER


def test_system_and_anonymous_carry_no_account() -> None:
    """Both mean "no account behind this row", and the two must stay apart: a
    scheduled job is not an unauthenticated caller."""
    assert Actor.system().actor_id is None
    assert Actor.system().ip is None
    assert Actor.anonymous(ip="10.0.0.1").actor_id is None
    assert Actor.anonymous(ip="10.0.0.1").actor_type is ActorType.ANONYMOUS


def test_an_actor_cannot_be_edited_after_it_is_built() -> None:
    """One request's actor is passed down several layers; a mutable one would
    let any of them re-attribute rows written further up."""
    actor = Actor.user(uuid4())

    try:
        actor.actor_type = ActorType.SYSTEM  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Actor must be frozen")
