import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import random
import time

import aiohttp
from aiohttp import TraceConfig, TraceRequestHeadersSentParams
from multidict import CIMultiDict
from yarl import URL

from loggers import get_logger
from src.core.http.errors import HttpTransportError
from src.core.http.interface import (
    CLIENT_DEFAULT,
    ClientDefault,
    QueryParams,
    RequestData,
    validate_request,
)
from src.core.http.options import (
    DEFAULT_LIMITS,
    DEFAULT_TIMEOUT,
    HttpLimits,
    HttpTimeout,
)
from src.core.http.response import HttpResponse
from src.core.http.retry import RetryPolicy, parse_retry_after
from src.core.utils.datetime_utils import get_utc_now

logger = get_logger(__name__)

READ_CHUNK_BYTES = 64 * 1024
# A process talks to a handful of providers. Far more open clients means a
# factory runs per call instead of once per process.
OPEN_CLIENTS_WARNING = 50

_open_clients: set["HttpClient"] = set()


async def close_http_clients() -> None:
    """Shutdown hook of the API and the worker: closes every session a client
    opened in this process."""
    for client in list(_open_clients):
        # One failing close must not keep the other sessions open, nor skip
        # the shutdown steps that follow this hook.
        try:
            await client.aclose()
        except Exception:
            logger.exception("Closing HTTP client %s failed", client.name)


def is_transient(error: Exception) -> bool:
    """False for any TLS failure (a certificate, a handshake the peer cut) and
    a malformed URL. A TLS error is a ClientConnectorError too, so this is not
    a question of the error's family; a reset mid-handshake is counted with
    them, since a retry within a call seldom outlives a broken TLS setup."""
    return not isinstance(error, aiohttp.ClientSSLError | aiohttp.InvalidURL)


def _too_large(operation: str) -> HttpTransportError:
    # Counted against the decoded body: a small gzip answer can expand past
    # any limit its Content-Length suggests.
    return HttpTransportError(
        operation, "ResponseTooLarge", request_sent=True, transient=False
    )


@dataclass(slots=True)
class _Progress:
    """Per-request flag set by the trace hook below. The error class cannot
    tell whether the server saw the request: a total deadline raises the same
    TimeoutError while waiting for a pooled connection and while reading."""

    headers_sent: bool = False


async def _mark_headers_sent(
    session: aiohttp.ClientSession,
    context: object,
    params: TraceRequestHeadersSentParams,
) -> None:
    # aiohttp fires this on an open connection, just before the bytes go out:
    # from here on the request may have reached the server.
    progress = getattr(context, "trace_request_ctx", None)
    if isinstance(progress, _Progress):
        progress.headers_sent = True


class HttpClient:
    """Outgoing HTTP to one third-party API.

    One session per client per process, opened on the first request, so a
    request pays no TLS handshake after the first. Every request carries
    `user_agent`: a WAF in front of a provider often refuses the library's
    default. The body is read before `request` returns, capped at
    `limits.max_response_bytes`. Logs name the operation and the host, never
    the path, which may hold a token.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        user_agent: str,
        headers: Mapping[str, str] | None = None,
        timeout: HttpTimeout = DEFAULT_TIMEOUT,
        limits: HttpLimits = DEFAULT_LIMITS,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("an HTTP client needs a User-Agent")
        self._headers = CIMultiDict(headers or {})
        if "User-Agent" in self._headers:
            raise ValueError("pass the User-Agent as user_agent, not in headers")
        self._headers["User-Agent"] = user_agent
        url = URL(base_url)
        if url.scheme not in ("http", "https") or not url.host:
            raise ValueError("base_url needs an http(s) scheme and a host")
        if "?" in base_url or "#" in base_url:
            raise ValueError("base_url cannot carry a query or a fragment")
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._host = url.host
        self._timeout = timeout
        self._limits = limits
        self._retry = retry
        self._sleep = sleep
        self._clock = clock
        self._rng = rng
        self._session: aiohttp.ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _http(self) -> aiohttp.ClientSession:
        loop = asyncio.get_running_loop()
        if self._session is not None and not self._session.closed:
            if self._loop is loop:
                return self._session
            if self._loop is not None and not self._loop.is_closed():
                # Only the loop that opened a session can close it; replacing
                # it here would leak its connection pool.
                raise RuntimeError(
                    f"HTTP client {self.name} is open in another running event "
                    "loop; close it there first (close_http_clients)"
                )
            logger.warning(
                "HTTP client %s dropped a session of a closed event loop", self.name
            )
        trace = TraceConfig()
        trace.on_request_headers_sent.append(_mark_headers_sent)
        self._session = aiohttp.ClientSession(
            headers=self._headers,
            trace_configs=[trace],
            timeout=self._timeout.to_aiohttp(),
            connector=aiohttp.TCPConnector(
                limit=self._limits.connections,
                limit_per_host=self._limits.connections_per_host,
                keepalive_timeout=self._limits.keepalive_seconds,
                ttl_dns_cache=self._limits.dns_cache_seconds,
            ),
        )
        self._loop = loop
        _open_clients.add(self)
        if len(_open_clients) % OPEN_CLIENTS_WARNING == 0:
            logger.warning(
                "%d HTTP clients open; build each client once per process",
                len(_open_clients),
            )
        return self._session

    async def aclose(self) -> None:
        _open_clients.discard(self)
        session, loop = self._session, self._loop
        self._session = None
        self._loop = None
        if session is None or session.closed:
            return
        if loop is asyncio.get_running_loop():
            await session.close()
        else:
            logger.warning(
                "HTTP client %s dropped a session of another event loop", self.name
            )

    async def request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: QueryParams | None = None,
        json: object = None,
        data: RequestData | None = None,
        headers: Mapping[str, str] | None = None,
        # Per-phase socket timeouts handed to aiohttp, not a deadline.
        timeout: HttpTimeout | None = None,  # noqa: ASYNC109
        retry: RetryPolicy | ClientDefault | None = CLIENT_DEFAULT,
    ) -> HttpResponse:
        policy = self._retry if isinstance(retry, ClientDefault) else retry
        validate_request(
            method,
            path,
            operation,
            policy=policy,
            headers=headers,
            params=params,
            json=json,
            data=data,
        )
        started = self._clock()
        attempt = 1
        while True:
            try:
                response = await self._exchange(
                    method,
                    path,
                    operation,
                    params=params,
                    json=json,
                    data=data,
                    headers=headers,
                    timeout=timeout or self._timeout,
                )
            except HttpTransportError as error:
                if policy is None or not policy.retries_error(
                    request_sent=error.request_sent, transient=error.transient
                ):
                    raise
                delay = self._next_delay(policy, attempt, started, retry_after=None)
                if delay is None:
                    raise
                cause = error.reason
            else:
                if policy is None or response.status not in policy.retry_statuses:
                    return response
                retry_after = (
                    parse_retry_after(
                        response.headers.get("Retry-After"), get_utc_now()
                    )
                    if policy.respect_retry_after
                    else None
                )
                delay = self._next_delay(
                    policy, attempt, started, retry_after=retry_after
                )
                if delay is None:
                    return response
                cause = f"status={response.status}"
            attempt += 1
            logger.info(
                "HTTP retry operation=%s attempt=%s delay=%.2fs after %s",
                operation,
                attempt,
                delay,
                cause,
            )
            await self._sleep(delay)

    def _next_delay(
        self,
        policy: RetryPolicy,
        attempt: int,
        started: float,
        *,
        retry_after: float | None,
    ) -> float | None:
        if attempt >= policy.max_attempts:
            return None
        if retry_after is None:
            delay = policy.backoff(attempt, self._rng)
        elif retry_after > policy.max_retry_after_seconds:
            return None
        else:
            delay = retry_after
        if self._clock() - started + delay > policy.max_elapsed_seconds:
            return None
        return delay

    async def _exchange(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        params: QueryParams | None,
        json: object,
        data: RequestData | None,
        headers: Mapping[str, str] | None,
        timeout: HttpTimeout,  # noqa: ASYNC109
    ) -> HttpResponse:
        started = self._clock()
        progress = _Progress()
        try:
            async with self._http().request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json,
                data=data,
                headers=headers,
                timeout=timeout.to_aiohttp(),
                # A redirect would re-send the body and any custom key header
                # (aiohttp strips only Authorization) to the host it names.
                allow_redirects=False,
                trace_request_ctx=progress,
            ) as response:
                body = await self._read_body(response, operation)
        except (aiohttp.ClientError, TimeoutError) as error:
            logger.debug(
                "HTTP %s operation=%s host=%s failed=%s elapsed_ms=%d",
                method,
                operation,
                self._host,
                type(error).__name__,
                (self._clock() - started) * 1000,
            )
            raise HttpTransportError(
                operation,
                type(error).__name__,
                request_sent=progress.headers_sent,
                transient=is_transient(error),
            ) from None
        logger.debug(
            "HTTP %s operation=%s host=%s status=%s elapsed_ms=%d",
            method,
            operation,
            self._host,
            response.status,
            (self._clock() - started) * 1000,
        )
        return HttpResponse(status=response.status, headers=response.headers, body=body)

    async def _read_body(
        self, response: aiohttp.ClientResponse, operation: str
    ) -> bytes:
        # Counted while streaming, never from Content-Length: a HEAD or a 304
        # states a length with no body behind it.
        limit = self._limits.max_response_bytes
        body = bytearray()
        async for chunk in response.content.iter_chunked(READ_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > limit:
                raise _too_large(operation)
        return bytes(body)
