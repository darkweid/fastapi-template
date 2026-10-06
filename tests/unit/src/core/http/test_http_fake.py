import pytest

from src.core.http.errors import HttpTransportError
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
