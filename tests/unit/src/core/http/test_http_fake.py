import pytest

from src.core.http.errors import HttpTransportError
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
