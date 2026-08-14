from __future__ import annotations

from typing import Any

import pytest

from src.core.proxy_headers import TrustedProxyHeadersMiddleware

TRUSTED_HOSTS = ["127.0.0.1", "10.0.0.0/8", "192.168.0.0/16"]


class ScopeRecorder:
    """Terminal ASGI app that stores the scope it was called with."""

    def __init__(self) -> None:
        self.scope: dict[str, Any] | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.scope = scope


async def call_middleware(
    *,
    client: tuple[str, int] | None,
    headers: dict[str, str] | None = None,
    trusted_hosts: list[str] | None = None,
    scheme: str = "http",
) -> dict[str, Any]:
    recorder = ScopeRecorder()
    middleware = TrustedProxyHeadersMiddleware(
        recorder,
        trusted_hosts=TRUSTED_HOSTS if trusted_hosts is None else trusted_hosts,
    )
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "client": client,
        "scheme": scheme,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        return None

    await middleware(scope, receive, send)
    assert recorder.scope is not None
    return recorder.scope


@pytest.mark.asyncio
async def test_untrusted_peer_keeps_client_and_scheme() -> None:
    scope = await call_middleware(
        client=("203.0.113.7", 40000),
        headers={
            "X-Forwarded-For": "1.2.3.4",
            "X-Forwarded-Proto": "https",
        },
    )

    assert scope["client"] == ("203.0.113.7", 40000)
    assert scope["scheme"] == "http"


@pytest.mark.asyncio
async def test_trusted_peer_takes_rightmost_untrusted_hop() -> None:
    scope = await call_middleware(
        client=("10.0.0.5", 40000),
        headers={"X-Forwarded-For": "198.51.100.9, 172.31.0.1"},
        trusted_hosts=[*TRUSTED_HOSTS, "172.16.0.0/12"],
    )

    assert scope["client"] == ("198.51.100.9", 40000)


@pytest.mark.asyncio
async def test_spoofed_left_entries_are_ignored() -> None:
    """
    nginx appends the real peer to the right, so anything the client injects on
    the left must never win.
    """
    scope = await call_middleware(
        client=("10.0.0.5", 40000),
        headers={"X-Forwarded-For": "9.9.9.9, 198.51.100.9"},
    )

    assert scope["client"] == ("198.51.100.9", 40000)


@pytest.mark.asyncio
async def test_chain_of_only_trusted_hops_falls_back_to_leftmost() -> None:
    scope = await call_middleware(
        client=("10.0.0.5", 40000),
        headers={"X-Forwarded-For": "192.168.1.10, 10.0.0.9"},
    )

    assert scope["client"] == ("192.168.1.10", 40000)


@pytest.mark.asyncio
async def test_malformed_hop_keeps_direct_peer() -> None:
    scope = await call_middleware(
        client=("10.0.0.5", 40000),
        headers={"X-Forwarded-For": "not-an-ip, 10.0.0.9"},
    )

    assert scope["client"] == ("10.0.0.5", 40000)


@pytest.mark.asyncio
async def test_trusted_peer_without_forwarded_for_keeps_client() -> None:
    scope = await call_middleware(
        client=("10.0.0.5", 40000),
        headers={"X-Forwarded-Proto": "https"},
    )

    assert scope["client"] == ("10.0.0.5", 40000)
    assert scope["scheme"] == "https"


@pytest.mark.asyncio
async def test_wildcard_trusts_the_whole_chain() -> None:
    scope = await call_middleware(
        client=("203.0.113.7", 40000),
        headers={"X-Forwarded-For": "9.9.9.9, 198.51.100.9"},
        trusted_hosts=["*"],
    )

    assert scope["client"] == ("9.9.9.9", 40000)


@pytest.mark.asyncio
async def test_ipv6_hop_is_accepted() -> None:
    scope = await call_middleware(
        client=("10.0.0.5", 40000),
        headers={"X-Forwarded-For": "2001:db8::1, 10.0.0.9"},
    )

    assert scope["client"] == ("2001:db8::1", 40000)


@pytest.mark.asyncio
async def test_missing_client_is_left_alone() -> None:
    scope = await call_middleware(
        client=None,
        headers={"X-Forwarded-For": "9.9.9.9"},
    )

    assert scope["client"] is None


@pytest.mark.asyncio
async def test_non_http_scope_passes_through() -> None:
    recorder = ScopeRecorder()
    middleware = TrustedProxyHeadersMiddleware(recorder, trusted_hosts=TRUSTED_HOSTS)
    scope: dict[str, Any] = {"type": "lifespan"}

    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(message: dict[str, Any]) -> None:
        return None

    await middleware(scope, receive, send)

    assert recorder.scope is scope
