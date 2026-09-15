from datetime import timedelta
from uuid import uuid4

from pydantic import ValidationError
import pytest

from src.core.utils.datetime_utils import get_utc_now
from src.event_log.enums import ActorType, ObjectType
from src.event_log.schemas import EventLogListParams


def test_blank_exact_filters_are_omitted() -> None:
    """A form submitting every field sends the untouched ones as empty strings."""
    params = EventLogListParams(
        actor_type="",
        actor_id="",
        object_type="",
        object_id="",
        event_type="   ",
    )

    assert params.to_filters() == {}


def test_only_the_filters_a_caller_set_reach_the_repository() -> None:
    """A `None` filter would be read as "column is null", not "not filtered"."""
    actor_id = uuid4()
    params = EventLogListParams(actor_type=ActorType.USER, actor_id=actor_id)

    assert params.to_filters() == {
        "actor_type": ActorType.USER,
        "actor_id": actor_id,
    }


def test_the_date_range_travels_in_the_list_query_not_the_filters() -> None:
    """`created_at` is a range, and an exact filter on it matches one instant."""
    date_from = get_utc_now() - timedelta(days=7)
    params = EventLogListParams(date_from=date_from, object_type=ObjectType.NOTE)

    query = params.to_list_query()

    assert query.date_from == date_from
    assert query.date_field == "created_at"
    assert "date_from" not in params.to_filters()


def test_search_is_not_a_parameter_of_this_resource() -> None:
    """No column is searchable here, so the parameter would only ever 400."""
    with pytest.raises(ValidationError):
        EventLogListParams(search="whatever")
