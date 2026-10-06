import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, timedelta
from email.utils import format_datetime
import gc
import gzip
import logging
import threading
from typing import Any
import warnings

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port
import pytest

from src.core.http import client as client_module
from src.core.http.client import HttpClient, close_http_clients, is_transient
from src.core.http.errors import HttpTransportError
from src.core.http.options import HttpLimits, HttpTimeout
from src.core.http.retry import RETRY_CONNECT_ONLY, RETRY_IDEMPOTENT, RetryPolicy
from src.core.utils.datetime_utils import get_utc_now

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


@asynccontextmanager
async def serve_raw(answer: bytes) -> AsyncIterator[str]:
    """A server that writes `answer` as is, for heads aiohttp.web rewrites."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(answer)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


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


async def test_a_head_of_a_large_resource_comes_back_as_a_status() -> None:
    """HEAD carries the length the GET would return, with no body to cap."""

    async def big(request: web.Request) -> web.Response:
        return web.Response(body=b"x" * 2048)

    async with serve(big) as server:
        client = client_for(server, limits=HttpLimits(max_response_bytes=1024))
        response = await client.request("HEAD", "/", operation="test.head")

    assert response.status == 200
    assert response.body == b""


async def test_a_not_modified_with_a_large_length_comes_back_as_a_status() -> None:
    """A 304 may state the representation's length (RFC 9110 8.6)."""
    answer = b"HTTP/1.1 304 Not Modified\r\nContent-Length: 52428800\r\n\r\n"
    async with serve_raw(answer) as base_url:
        client = HttpClient(
            name="test",
            base_url=base_url,
            user_agent=USER_AGENT,
            limits=HttpLimits(max_response_bytes=1024),
        )
        response = await client.request(
            "GET", "/", operation="test.get", headers={"If-None-Match": '"v1"'}
        )

    assert response.status == 304


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


def test_a_user_agent_among_the_default_headers_is_refused() -> None:
    """The agent has one source; a second spelling would be dropped silently
    or sent twice, which a strict server answers with 400."""
    with pytest.raises(ValueError, match="User-Agent"):
        HttpClient(
            name="test",
            base_url="http://x",
            user_agent=USER_AGENT,
            headers={"user-agent": "other/1"},
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "notify.eskiz.uz/api",
        "ftp://files.example",
        "https:///api",
        "https://api.example/?key=1",
        "https://api.example/#top",
        "https://api.example/api?",
        "https://api.example/api#",
    ],
)
def test_a_base_url_that_cannot_prefix_a_path_is_refused(base_url: str) -> None:
    """Caught where the client is built, not on the first send in production."""
    with pytest.raises(ValueError, match="base_url"):
        HttpClient(name="test", base_url=base_url, user_agent=USER_AGENT)


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


async def test_request_headers_cannot_replace_the_user_agent() -> None:
    """The configured agent is the one a provider's WAF lets through."""
    with pytest.raises(ValueError, match="User-Agent"):
        await refused_client().request(
            "GET", "/", operation="test.get", headers={"user-agent": "other/1"}
        )


def test_a_session_open_in_another_loop_is_refused_not_dropped() -> None:
    """Replacing it would leak its pool: only its own loop can close it."""
    client = refused_client()
    first = asyncio.Runner()
    try:
        with pytest.raises(HttpTransportError):
            first.run(client.request("GET", "/", operation="test.get"))

        with asyncio.Runner() as second, pytest.raises(RuntimeError, match="loop"):
            second.run(client.request("GET", "/", operation="test.get"))
    finally:
        first.run(client.aclose())
        first.close()


def test_a_close_from_another_loop_keeps_a_session_its_idle_loop_still_owns() -> None:
    """Dropping it would strand its pool where no later close can reach it."""
    client = refused_client()
    first = asyncio.Runner()
    try:
        with pytest.raises(HttpTransportError):
            first.run(client.request("GET", "/", operation="test.get"))
        session = client._session  # noqa: SLF001

        with asyncio.Runner() as second, pytest.raises(RuntimeError, match="loop"):
            second.run(client.aclose())
        assert session is not None
        assert not session.closed

        first.run(client.aclose())
        assert session.closed
    finally:
        first.close()


def test_a_close_from_another_thread_closes_on_the_owning_loop() -> None:
    """A worker thread's loop owns the session; shutdown runs elsewhere."""
    client = refused_client()
    owner = asyncio.new_event_loop()
    thread = threading.Thread(target=owner.run_forever)
    thread.start()
    try:
        opened = asyncio.run_coroutine_threadsafe(
            client.request("GET", "/", operation="test.get"), owner
        )
        with pytest.raises(HttpTransportError):
            opened.result(timeout=5)
        session = client._session  # noqa: SLF001

        asyncio.run(close_http_clients())

        assert session is not None
        assert session.closed
        assert client._session is None  # noqa: SLF001
    finally:
        owner.call_soon_threadsafe(owner.stop)
        thread.join()
        owner.close()


async def test_a_request_that_never_left_the_pool_counts_as_not_sent() -> None:
    """A deadline spent waiting for a pooled connection sent nothing, so a
    connect-only policy may repeat it, POST included."""
    arrived, release = asyncio.Event(), asyncio.Event()

    async def held(request: web.Request) -> web.Response:
        arrived.set()
        await release.wait()
        return web.Response()

    async with serve(held) as server:
        client = client_for(
            server, limits=HttpLimits(connections=1, connections_per_host=1)
        )
        holder = asyncio.create_task(client.request("GET", "/", operation="test.hold"))
        await arrived.wait()
        with pytest.raises(HttpTransportError) as caught:
            await client.request(
                "POST",
                "/",
                operation="test.post",
                timeout=HttpTimeout(total_seconds=0.1),
            )
        release.set()
        await holder

    assert caught.value.request_sent is False
    assert caught.value.transient is True


async def test_a_compressed_answer_is_capped_after_decoding() -> None:
    """A small gzip body can expand far past the limit its length suggests."""

    async def bomb(request: web.Request) -> web.Response:
        return web.Response(
            body=gzip.compress(b"x" * 4096), headers={"Content-Encoding": "gzip"}
        )

    async with serve(bomb) as server:
        client = client_for(server, limits=HttpLimits(max_response_bytes=1024))
        with pytest.raises(HttpTransportError) as caught:
            await client.request("GET", "/", operation="test.get")

    assert caught.value.reason == "ResponseTooLarge"


async def test_a_retry_after_date_sets_the_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps = Sleeps()
    now = get_utc_now().astimezone(UTC).replace(microsecond=0)
    monkeypatch.setattr(client_module, "get_utc_now", lambda: now)
    moment = format_datetime(now + timedelta(seconds=3), usegmt=True)
    handler, _ = statuses(503, 200, headers={"Retry-After": moment})

    async with serve(handler) as server:
        client = client_for(server, retry=RETRY_IDEMPOTENT, sleep=sleeps)
        await client.request("GET", "/", operation="test.get")

    assert sleeps.calls == [3.0]


async def test_an_unreadable_retry_after_falls_back_to_backoff() -> None:
    sleeps = Sleeps()
    handler, _ = statuses(503, 200, headers={"Retry-After": "soon"})

    async with serve(handler) as server:
        client = client_for(
            server, retry=RETRY_IDEMPOTENT, sleep=sleeps, rng=lambda: 1.0
        )
        await client.request("GET", "/", operation="test.get")

    assert sleeps.calls == [0.5]


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def client_log() -> Iterator[Records]:
    target = logging.getLogger("src.core.http.client")
    records, level = Records(), target.level
    target.addHandler(records)
    target.setLevel(logging.DEBUG)
    yield records
    target.removeHandler(records)
    target.setLevel(level)


async def test_logs_name_the_failure_and_never_the_path(client_log: Records) -> None:
    """The Telegram path holds the bot token; an outage must still be
    readable from the client's own lines."""
    handler, _ = statuses(503, 200)

    async with serve(handler) as server:
        client = client_for(server, retry=RETRY_IDEMPOTENT, sleep=Sleeps())
        await client.request("GET", "/bot1:secret/getMe", operation="telegram.get_me")
    with pytest.raises(HttpTransportError):
        await refused_client(retry=RETRY_CONNECT_ONLY, sleep=Sleeps()).request(
            "POST", "/bot1:secret/sendMessage", operation="telegram.send"
        )

    assert not [line for line in client_log.lines if "secret" in line]
    assert any("status=503" in line for line in client_log.lines)
    assert any("ClientConnectorError" in line for line in client_log.lines)


@pytest.mark.parametrize(
    ("error_class", "transient"),
    [
        (aiohttp.ClientConnectorCertificateError, False),
        (aiohttp.ClientConnectorSSLError, False),
        (aiohttp.InvalidURL, False),
        (aiohttp.ClientConnectorError, True),
        (aiohttp.ServerDisconnectedError, True),
        (TimeoutError, True),
    ],
)
def test_only_a_failure_a_retry_cannot_fix_is_permanent(
    error_class: type[Exception], transient: bool
) -> None:
    """A certificate error is a ClientConnectorError too: it must not be
    retried as a refused connection would be."""
    error = error_class.__new__(error_class)

    assert is_transient(error) is transient


async def test_repeated_and_numeric_query_keys_are_sent() -> None:
    """Provider APIs take `?id=1&id=2`; a mapping of strings cannot say it."""
    seen: list[list[str]] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.query.getall("id"))
        return web.Response()

    async with serve(handler) as server:
        await client_for(server).request(
            "GET", "/", operation="test.get", params=[("id", 1), ("id", "2")]
        )

    assert seen == [["1", "2"]]


async def test_a_boolean_query_value_is_refused() -> None:
    """yarl refuses a bool at send time; the check makes the fake refuse it too."""
    with pytest.raises(ValueError, match="bool"):
        await refused_client().request(
            "GET", "/", operation="test.get", params={"active": True}
        )


def test_a_session_whose_loop_has_closed_is_dropped_and_reopened(
    client_log: Records,
) -> None:
    """Two `asyncio.run` calls in a script share a cached client; the first
    loop is gone, so its session can only be dropped, and that is logged."""
    client = refused_client()
    with asyncio.Runner() as first, pytest.raises(HttpTransportError):
        first.run(client.request("GET", "/", operation="test.get"))

    # The dropped session warns as unclosed when collected; collect it here so
    # the warning cannot land in whichever test runs next.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        with asyncio.Runner() as second:
            with pytest.raises(HttpTransportError):
                second.run(client.request("GET", "/", operation="test.get"))
            second.run(client.aclose())
        gc.collect()

    assert any("closed event loop" in line for line in client_log.lines)


async def test_many_open_clients_are_reported(client_log: Records) -> None:
    """A factory run per request instead of once per process opens a session
    per call; the registry keeps each until shutdown."""
    clients = [refused_client() for _ in range(client_module.OPEN_CLIENTS_WARNING)]
    for client in clients:
        client._http()  # noqa: SLF001

    assert any("HTTP clients open" in line for line in client_log.lines)
