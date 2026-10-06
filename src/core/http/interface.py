from collections.abc import Mapping
from enum import Enum, auto
from typing import Protocol

from src.core.http.options import HttpTimeout
from src.core.http.response import HttpResponse
from src.core.http.retry import RetryPolicy


class ClientDefault(Enum):
    """`retry=CLIENT_DEFAULT` applies the client's own policy; `None` means
    no retries for that request."""

    POLICY = auto()


CLIENT_DEFAULT = ClientDefault.POLICY


class HttpRequester(Protocol):
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
        """Raises `HttpTransportError` when no status came back; a status,
        any status, is returned."""
        ...

    async def aclose(self) -> None: ...
