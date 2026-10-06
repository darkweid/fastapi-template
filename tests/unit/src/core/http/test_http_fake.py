import traceback

import pytest

from src.core.http.errors import HttpTransportError
from src.core.http.options import HttpTimeout
from src.core.http.retry import RETRY_IDEMPOTENT
from tests.fakes.http import FakeHttpClient, fake_response


async def test_the_fake_records_and_answers_in_order_repeating_the_last() -> None:
    http = FakeHttpClient(fake_response(503), fake_response(200, json={"ok": True}))

    first = await http.request("GET", "/a", operation="x.a")
    second = await http.request("POST", "/b", operation="x.b", json={"n": 1})
    third = await http.request("GET", "/c", operation="x.c")

    assert [first.status, second.status, third.status] == [503, 200, 200]
    assert second.json() == {"ok": True}
    assert [(r.method, r.path, r.json) for r in http.requests] == [
        ("GET", "/a", None),
        ("POST", "/b", {"n": 1}),
        ("GET", "/c", None),
    ]


async def test_the_fake_raises_a_queued_error() -> None:
    error = HttpTransportError(
        "x", "ClientConnectorError", request_sent=False, transient=True
    )
    http = FakeHttpClient(error)

    with pytest.raises(HttpTransportError):
        await http.request("GET", "/", operation="x")


async def test_the_fake_refuses_a_relative_path_like_the_client() -> None:
    """The fake is the only HTTP an integration's tests see: a call the real
    client refuses must fail there too, not first in production."""
    with pytest.raises(ValueError):
        await FakeHttpClient().request("POST", "sendMessage", operation="x")


async def test_the_fake_refuses_a_post_under_a_repeating_policy() -> None:
    with pytest.raises(ValueError, match="idempotent"):
        await FakeHttpClient().request(
            "POST", "/", operation="x", retry=RETRY_IDEMPOTENT
        )


async def test_the_fake_refuses_a_user_agent_override_like_the_client() -> None:
    with pytest.raises(ValueError, match="User-Agent"):
        await FakeHttpClient().request(
            "GET", "/", operation="x", headers={"User-Agent": "other/1"}
        )


async def test_the_fake_checks_its_client_policy_too() -> None:
    """A factory that gives a POST-only provider a repeating policy fails every
    send in production; the fake built with that policy fails the same way."""
    with pytest.raises(ValueError, match="idempotent"):
        await FakeHttpClient(retry=RETRY_IDEMPOTENT).request("POST", "/", operation="x")


async def test_the_fake_refuses_json_and_data_together_like_the_client() -> None:
    with pytest.raises(ValueError, match="json"):
        await FakeHttpClient().request(
            "POST", "/", operation="x", json={"a": 1}, data={"b": "2"}
        )


async def test_the_fake_records_the_retry_and_timeout_of_a_call() -> None:
    """An integration chooses both per call; its tests have to see the choice."""
    http = FakeHttpClient()
    deadline = HttpTimeout(total_seconds=2)

    await http.request("GET", "/", operation="x", retry=None, timeout=deadline)

    assert (http.requests[0].retry, http.requests[0].timeout) == (None, deadline)


async def test_the_fake_refuses_a_boolean_query_value_like_the_client() -> None:
    with pytest.raises(ValueError, match="bool"):
        await FakeHttpClient(fake_response(200)).request(
            "GET", "/", operation="test.get", params=[("active", False)]
        )


async def test_a_repeated_exception_does_not_grow_its_traceback() -> None:
    http = FakeHttpClient(HttpTransportError("test.get", "Boom"))
    depths = []
    for _ in range(3):
        with pytest.raises(HttpTransportError) as caught:
            await http.request("GET", "/", operation="test.get")
        depths.append(len(traceback.extract_tb(caught.value.__traceback__)))

    assert depths[0] == depths[-1]
