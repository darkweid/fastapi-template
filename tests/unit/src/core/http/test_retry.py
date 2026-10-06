from dataclasses import replace
from datetime import UTC, timedelta
from email.utils import format_datetime

import pytest

from src.core.http.retry import (
    RETRY_CONNECT_ONLY,
    RETRY_IDEMPOTENT,
    RetryPolicy,
    parse_retry_after,
)
from src.core.utils.datetime_utils import get_utc_now


def test_backoff_doubles_up_to_its_cap() -> None:
    policy = RetryPolicy(backoff_base_seconds=0.5, backoff_max_seconds=8)

    delays = [policy.backoff(attempt, lambda: 1.0) for attempt in range(1, 8)]

    assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_backoff_is_full_jitter() -> None:
    """Clients that failed together must not come back together."""
    assert RetryPolicy().backoff(3, lambda: 0.0) == 0.0
    assert RetryPolicy().backoff(3, lambda: 0.5) == 1.0


@pytest.mark.parametrize("method", ["POST", "post", "PATCH"])
def test_a_policy_repeating_sent_requests_refuses_a_non_idempotent_method(
    method: str,
) -> None:
    """A retried POST that already reached the server charges or sends twice."""
    with pytest.raises(ValueError, match="idempotent"):
        RETRY_IDEMPOTENT.ensure_allows(method)


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "PUT", "DELETE"])
def test_an_idempotent_method_takes_any_policy(method: str) -> None:
    RETRY_IDEMPOTENT.ensure_allows(method)


def test_connect_only_retries_are_safe_on_post() -> None:
    RETRY_CONNECT_ONLY.ensure_allows("POST")


def test_an_idempotency_key_api_may_opt_in_to_repeating_post() -> None:
    replace(RETRY_IDEMPOTENT, allow_non_idempotent=True).ensure_allows("POST")


@pytest.mark.parametrize(
    ("policy", "request_sent", "transient", "expected"),
    [
        (RETRY_CONNECT_ONLY, False, True, True),
        (RETRY_CONNECT_ONLY, True, True, False),
        (RETRY_IDEMPOTENT, True, True, True),
        (RETRY_IDEMPOTENT, False, False, False),
    ],
)
def test_which_transport_errors_a_policy_retries(
    policy: RetryPolicy, request_sent: bool, transient: bool, expected: bool
) -> None:
    assert (
        policy.retries_error(request_sent=request_sent, transient=transient) is expected
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"backoff_base_seconds": 9, "backoff_max_seconds": 8},
        {"max_elapsed_seconds": -1},
        {"retry_statuses": frozenset({99})},
    ],
)
def test_an_invalid_policy_fails_where_it_is_declared(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_retry_after_in_seconds() -> None:
    assert parse_retry_after("3", get_utc_now()) == 3.0


def test_retry_after_as_an_http_date() -> None:
    now = get_utc_now().astimezone(UTC).replace(microsecond=0)
    header = format_datetime(now + timedelta(seconds=10), usegmt=True)

    assert parse_retry_after(header, now) == 10.0


def test_a_retry_after_date_in_the_past_means_now() -> None:
    now = get_utc_now().astimezone(UTC).replace(microsecond=0)
    header = format_datetime(now - timedelta(seconds=10), usegmt=True)

    assert parse_retry_after(header, now) == 0.0


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "soon",
        "-1",
        "1.5",
        "²",
        "Sun, 06 Nov 999999999999 08:49:37 GMT",
        "Sun, 06 Nov 10000 08:49:37 GMT",
    ],
)
def test_an_unreadable_retry_after_is_ignored(value: str | None) -> None:
    assert parse_retry_after(value, get_utc_now()) is None
