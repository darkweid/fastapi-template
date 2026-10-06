import asyncio
from collections.abc import Awaitable, Callable, Mapping
import random
import time

import aiohttp
from multidict import CIMultiDict
from yarl import URL

from loggers import get_logger
from src.core.http.errors import HttpTransportError
from src.core.http.interface import CLIENT_DEFAULT, ClientDefault
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


def validate_request(
    method: str, path: str, operation: str, policy: RetryPolicy | None
) -> None:
    """The checks every `HttpRequester` runs before sending, so a test fake
    refuses what the real client refuses."""
    if not path.startswith("/"):
        raise ValueError(f"{operation}: the path must start with '/'")
    if policy is not None:
        policy.ensure_allows(method)


def _transport_error(operation: str, error: Exception) -> HttpTransportError:
    reason = type(error).__name__
    # A certificate error is a ClientConnectorError too, so it is checked
    # first: the server never saw the request, and no retry will fix it.
    if isinstance(error, aiohttp.ClientSSLError | aiohttp.InvalidURL):
        return HttpTransportError(
            operation, reason, request_sent=False, transient=False
        )
    if isinstance(error, aiohttp.ClientConnectorError | aiohttp.ConnectionTimeoutError):
        return HttpTransportError(operation, reason, request_sent=False, transient=True)
    return HttpTransportError(operation, reason, request_sent=True, transient=True)


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
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._host = URL(self._base_url).host or ""
        # Case-insensitive, so a `user-agent` in `headers` is replaced, never
        # sent beside the configured one.
        self._headers = CIMultiDict(headers or {})
        self._headers["User-Agent"] = user_agent
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
        # A session is bound to the loop it was opened on. A new loop (each
        # test gets one) can neither use the old session nor close it.
        if self._session is None or self._session.closed or self._loop is not loop:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
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
        return self._session

    async def aclose(self) -> None:
        _open_clients.discard(self)
        session, loop = self._session, self._loop
        self._session = None
        self._loop = None
        if session is not None and loop is asyncio.get_running_loop():
            await session.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: Mapping[str, str] | None = None,
        json: object = None,
        data: Mapping[str, str] | bytes | None = None,
        headers: Mapping[str, str] | None = None,
        # Per-phase socket timeouts handed to aiohttp, not a deadline.
        timeout: HttpTimeout | None = None,  # noqa: ASYNC109
        retry: RetryPolicy | ClientDefault | None = CLIENT_DEFAULT,
    ) -> HttpResponse:
        policy = self._retry if isinstance(retry, ClientDefault) else retry
        validate_request(method, path, operation, policy)
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
            attempt += 1
            logger.info(
                "HTTP retry operation=%s attempt=%s delay=%.2fs",
                operation,
                attempt,
                delay,
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
        params: Mapping[str, str] | None,
        json: object,
        data: Mapping[str, str] | bytes | None,
        headers: Mapping[str, str] | None,
        timeout: HttpTimeout,  # noqa: ASYNC109
    ) -> HttpResponse:
        started = self._clock()
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
            ) as response:
                body = await self._read_body(response, operation)
        except (aiohttp.ClientError, TimeoutError) as error:
            raise _transport_error(operation, error) from None
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
        limit = self._limits.max_response_bytes
        too_large = HttpTransportError(
            operation, "ResponseTooLarge", request_sent=True, transient=False
        )
        if response.content_length is not None and response.content_length > limit:
            raise too_large
        body = bytearray()
        async for chunk in response.content.iter_chunked(READ_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > limit:
                raise too_large
        return bytes(body)
