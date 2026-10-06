import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port
import pytest

from src.core.http.client import HttpClient, close_http_clients
from src.core.http.errors import HttpTransportError
from src.core.http.options import HttpLimits, HttpTimeout
from src.core.http.retry import RETRY_CONNECT_ONLY, RETRY_IDEMPOTENT, RetryPolicy

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
USER_AGENT = "template-tests/1.0"


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


@asynccontextmanager
async def serve(handler: Handler) -> AsyncIterator[TestServer]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        yield server
    finally:
        await server.close()


def client_for(server: TestServer, **kwargs: Any) -> HttpClient:
    return HttpClient(
        name="test",
        base_url=str(server.make_url("/")),
        user_agent=USER_AGENT,
        **kwargs,
    )


def refused_client(**kwargs: Any) -> HttpClient:
    return HttpClient(
        name="test",
        base_url=f"http://127.0.0.1:{unused_port()}",
        user_agent=USER_AGENT,
        **kwargs,
    )


def statuses(
    *codes: int, headers: dict[str, str] | None = None
) -> tuple[Handler, list[int]]:
    """Answers with each code in turn, repeating the last; counts the calls."""
    queue, calls = list(codes), [0]

    async def handler(request: web.Request) -> web.Response:
        calls[0] += 1
        code = queue.pop(0) if len(queue) > 1 else queue[0]
        return web.Response(status=code, headers=headers if code != 200 else None)

    return handler, calls


async def test_requests_carry_the_configured_user_agent_only() -> None:
    """A WAF in front of a provider refuses aiohttp's own `Python/x aiohttp/x`."""
    seen: list[list[str]] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.headers.getall("User-Agent"))
        return web.Response()

    async with serve(handler) as server:
        await client_for(server).request("GET", "/", operation="test.get")

    assert seen == [[USER_AGENT]]


async def test_request_headers_add_to_the_client_defaults() -> None:
    seen: list[tuple[str | None, str | None]] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(
            (request.headers.get("Authorization"), request.headers.get("X-Trace"))
        )
        return web.Response()

    async with serve(handler) as server:
        client = client_for(server, headers={"Authorization": "Key k"})
        await client.request("GET", "/", operation="test.get", headers={"X-Trace": "1"})

    assert seen == [("Key k", "1")]


async def test_the_answer_comes_back_read() -> None:
    received: list[object] = []

    async def handler(request: web.Request) -> web.Response:
        received.append((request.path, request.query.get("c"), await request.json()))
        return web.Response(status=201, body=b'{"id": 7}', headers={"X-Id": "7"})

    async with serve(handler) as server:
        response = await client_for(server).request(
            "POST",
            "/items",
            operation="test.create",
            params={"c": "push"},
            json={"a": 1},
        )

    assert received == [("/items", "push", {"a": 1})]
    assert response.status == 201
    assert response.json() == {"id": 7}
    assert response.headers["x-id"] == "7"


async def test_a_status_is_returned_not_raised() -> None:
    handler, calls = statuses(503)

    async with serve(handler) as server:
        response = await client_for(server).request("GET", "/", operation="test.get")

    assert (response.status, calls[0]) == (503, 1)


async def test_requests_share_one_connection() -> None:
    """A connection per request paid a TLS handshake each time: 70 of the
    100 ms a Telegram send took from stage."""
    peers: list[object] = []

    async def handler(request: web.Request) -> web.Response:
        assert request.transport is not None
        peers.append(request.transport.get_extra_info("peername"))
        return web.Response()

    async with serve(handler) as server:
        client = client_for(server)
        for _ in range(3):
            await client.request("GET", "/", operation="test.get")

    assert len(set(map(str, peers))) == 1


async def test_closing_every_client_drops_its_connection_and_it_reopens() -> None:
    peers: list[object] = []

    async def handler(request: web.Request) -> web.Response:
        assert request.transport is not None
        peers.append(request.transport.get_extra_info("peername"))
        return web.Response()

    async with serve(handler) as server:
        client = client_for(server)
        await client.request("GET", "/", operation="test.get")
        await close_http_clients()
        await client.request("GET", "/", operation="test.get")

    assert peers[0] != peers[1]


async def test_a_refused_connection_never_quotes_the_url() -> None:
    """The Telegram path holds the bot token; aiohttp's message quotes the URL."""
    with pytest.raises(HttpTransportError) as caught:
        await refused_client().request(
            "POST", "/bot1:secret/sendMessage", operation="telegram.send"
        )

    error = caught.value
    assert error.message == "telegram.send: ClientConnectorError"
    assert (error.request_sent, error.transient) == (False, True)
    assert error.__cause__ is None
    assert error.__suppress_context__


async def test_a_read_timeout_counts_as_sent_and_connect_only_does_not_repeat_it() -> (
    None
):
    calls = [0]

    async def slow(request: web.Request) -> web.Response:
        calls[0] += 1
        await asyncio.sleep(1)
        return web.Response()

    async with serve(slow) as server:
        client = client_for(
            server, timeout=HttpTimeout(read_seconds=0.05), retry=RETRY_CONNECT_ONLY
        )
        with pytest.raises(HttpTransportError) as caught:
            await client.request("POST", "/", operation="test.post")

    assert caught.value.request_sent is True
    assert calls[0] == 1


async def test_an_oversized_answer_is_refused() -> None:
    async def big(request: web.Request) -> web.Response:
        return web.Response(body=b"x" * 2048)

    async with serve(big) as server:
        client = client_for(server, limits=HttpLimits(max_response_bytes=1024))
        with pytest.raises(HttpTransportError) as caught:
            await client.request("GET", "/", operation="test.get")

    assert caught.value.reason == "ResponseTooLarge"
    assert caught.value.transient is False


async def test_a_chunked_answer_without_a_length_is_capped_too() -> None:
    async def stream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        response.enable_chunked_encoding()
        await response.prepare(request)
        for _ in range(4):
            await response.write(b"x" * 512)
        await response.write_eof()
        return response

    async with serve(stream) as server:
        client = client_for(server, limits=HttpLimits(max_response_bytes=1024))
        with pytest.raises(HttpTransportError) as caught:
            await client.request("GET", "/", operation="test.get")

    assert caught.value.reason == "ResponseTooLarge"


def test_a_client_without_a_user_agent_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="User-Agent"):
        HttpClient(name="test", base_url="http://x", user_agent=" ")


async def test_a_path_must_be_absolute() -> None:
    with pytest.raises(ValueError):
        await refused_client().request("GET", "items", operation="test.get")


async def test_connect_only_repeats_a_refused_connection_with_backoff() -> None:
    sleeps = Sleeps()
    client = refused_client(retry=RETRY_CONNECT_ONLY, sleep=sleeps, rng=lambda: 1.0)

    with pytest.raises(HttpTransportError):
        await client.request("POST", "/", operation="test.post")

    assert sleeps.calls == [0.5, 1.0]


async def test_idempotent_policy_repeats_a_retryable_status_until_success() -> None:
    sleeps = Sleeps()
    handler, calls = statuses(503, 503, 200)

    async with serve(handler) as server:
        client = client_for(server, sleep=sleeps, rng=lambda: 1.0)
        response = await client.request(
            "GET", "/", operation="test.get", retry=RETRY_IDEMPOTENT
        )

    assert (response.status, calls[0]) == (200, 3)
    assert sleeps.calls == [0.5, 1.0]


async def test_the_last_answer_comes_back_when_attempts_run_out() -> None:
    handler, calls = statuses(503)

    async with serve(handler) as server:
        client = client_for(server, retry=RETRY_IDEMPOTENT, sleep=Sleeps())
        response = await client.request("GET", "/", operation="test.get")

    assert (response.status, calls[0]) == (503, 3)


async def test_connect_only_never_repeats_a_status() -> None:
    sleeps = Sleeps()
    handler, calls = statuses(503)

    async with serve(handler) as server:
        client = client_for(server, retry=RETRY_CONNECT_ONLY, sleep=sleeps)
        response = await client.request("POST", "/", operation="test.post")

    assert (response.status, calls[0], sleeps.calls) == (503, 1, [])


async def test_retry_after_sets_the_pause() -> None:
    sleeps = Sleeps()
    handler, _ = statuses(429, 200, headers={"Retry-After": "2"})

    async with serve(handler) as server:
        client = client_for(server, retry=RETRY_IDEMPOTENT, sleep=sleeps)
        response = await client.request("GET", "/", operation="test.get")

    assert response.status == 200
    assert sleeps.calls == [2.0]


async def test_a_retry_after_past_the_cap_hands_the_answer_back() -> None:
    """Sleeping two minutes inside a request would hold a worker slot for
    nothing; the caller decides what a long wait means."""
    sleeps = Sleeps()
    handler, calls = statuses(429, headers={"Retry-After": "120"})

    async with serve(handler) as server:
        client = client_for(server, retry=RETRY_IDEMPOTENT, sleep=sleeps)
        response = await client.request("GET", "/", operation="test.get")

    assert (response.status, calls[0], sleeps.calls) == (429, 1, [])


async def test_a_post_under_a_repeating_policy_is_refused_before_sending() -> None:
    handler, calls = statuses(200)

    async with serve(handler) as server:
        client = client_for(server, retry=RETRY_IDEMPOTENT)
        with pytest.raises(ValueError, match="idempotent"):
            await client.request("POST", "/", operation="test.post")

    assert calls[0] == 0


async def test_an_idempotency_key_api_may_repeat_a_post() -> None:
    handler, calls = statuses(503, 200)

    async with serve(handler) as server:
        client = client_for(
            server,
            retry=replace(RETRY_IDEMPOTENT, allow_non_idempotent=True),
            sleep=Sleeps(),
        )
        response = await client.request("POST", "/", operation="test.post")

    assert (response.status, calls[0]) == (200, 2)


async def test_none_on_a_request_switches_the_client_policy_off() -> None:
    handler, calls = statuses(503)

    async with serve(handler) as server:
        client = client_for(server, retry=RETRY_IDEMPOTENT, sleep=Sleeps())
        await client.request("GET", "/", operation="test.get", retry=None)

    assert calls[0] == 1


async def test_no_retry_starts_past_the_elapsed_budget() -> None:
    sleeps = Sleeps()
    handler, calls = statuses(503)
    policy = RetryPolicy(retry_statuses=frozenset({503}), max_elapsed_seconds=0.6)

    async with serve(handler) as server:
        client = client_for(
            server, retry=policy, sleep=sleeps, clock=lambda: 0.0, rng=lambda: 1.0
        )
        await client.request("GET", "/", operation="test.get")

    assert (calls[0], sleeps.calls) == (2, [0.5])


async def test_a_redirect_comes_back_as_a_status() -> None:
    """Following it would re-send the body and any custom key header (aiohttp
    strips only Authorization) to whatever host the Location names."""
    elsewhere, elsewhere_calls = statuses(200)

    async with serve(elsewhere) as other:

        async def redirect(request: web.Request) -> web.Response:
            raise web.HTTPTemporaryRedirect(str(other.make_url("/land")))

        async with serve(redirect) as server:
            client = client_for(server, headers={"X-Api-Key": "secret"})
            response = await client.request("POST", "/", operation="test.post")

    assert response.status == 307
    assert elsewhere_calls[0] == 0


async def test_a_user_agent_in_any_spelling_is_replaced_not_doubled() -> None:
    """Two User-Agent headers make a strict server answer 400."""
    seen: list[list[str]] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.headers.getall("User-Agent"))
        return web.Response()

    async with serve(handler) as server:
        client = client_for(server, headers={"user-agent": "other/1"})
        await client.request("GET", "/", operation="test.get")

    assert seen == [[USER_AGENT]]


async def test_one_failing_close_does_not_keep_the_others_open() -> None:
    """The shutdown hooks after close_http_clients (broker, cache, Redis)
    must run whatever one provider session does on close."""
    handler, _ = statuses(200)

    async with serve(handler) as server:
        broken, healthy = client_for(server), client_for(server)
        await broken.request("GET", "/", operation="test.get")
        await healthy.request("GET", "/", operation="test.get")

        async def fail() -> None:
            raise RuntimeError("close failed")

        broken.aclose = fail  # type: ignore[method-assign]
        await close_http_clients()
        broken.aclose = HttpClient.aclose.__get__(broken)  # type: ignore[method-assign]

        assert healthy._session is None  # noqa: SLF001
