from collections.abc import Mapping, Sequence
from enum import Enum, auto
from typing import Protocol

from multidict import CIMultiDict

from src.core.http.options import HttpTimeout
from src.core.http.response import HttpResponse
from src.core.http.retry import RetryPolicy


class ClientDefault(Enum):
    """`retry=CLIENT_DEFAULT` applies the client's own policy; `None` means
    no retries for that request."""

    POLICY = auto()


CLIENT_DEFAULT = ClientDefault.POLICY

# A sequence of pairs carries a repeated key (`?id=1&id=2`).
QueryParams = Mapping[str, str | int] | Sequence[tuple[str, str | int]]
# A mapping is sent as an urlencoded form; bytes and str as they are.
RequestData = Mapping[str, str] | bytes | str


def validate_request(
    method: str,
    path: str,
    operation: str,
    *,
    policy: RetryPolicy | None,
    headers: Mapping[str, str] | None,
    json: object,
    data: RequestData | None,
) -> None:
    """The checks every `HttpRequester` runs before sending, so a test fake
    refuses what the real client refuses."""
    if not path.startswith("/"):
        raise ValueError(f"{operation}: the path must start with '/'")
    if headers is not None and "User-Agent" in CIMultiDict(headers):
        raise ValueError(f"{operation}: the User-Agent is set on the client only")
    if json is not None and data is not None:
        raise ValueError(f"{operation}: send either json or data, not both")
    if policy is not None:
        policy.ensure_allows(method)


class HttpRequester(Protocol):
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
        """Raises `HttpTransportError` when no status came back; a status,
        any status, is returned."""
        ...

    async def aclose(self) -> None: ...
