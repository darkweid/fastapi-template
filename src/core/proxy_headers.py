from __future__ import annotations

from collections.abc import Sequence
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)

from starlette.types import ASGIApp, Receive, Scope, Send

TrustedNetwork = IPv4Network | IPv6Network
TrustedAddress = IPv4Address | IPv6Address


def _normalize_trusted_hosts(
    values: Sequence[str],
) -> tuple[bool, list[TrustedNetwork], set[str]]:
    trust_all = False
    networks: list[TrustedNetwork] = []
    literals: set[str] = set()

    for raw_value in values:
        value = raw_value.strip()
        if not value:
            continue
        if value == "*":
            trust_all = True
            continue
        try:
            networks.append(ip_network(value, strict=False))
            continue
        except ValueError:
            literals.add(value)

    return trust_all, networks, literals


def _is_trusted_host(
    client_host: str,
    trust_all: bool,
    networks: Sequence[TrustedNetwork],
    literals: set[str],
) -> bool:
    if trust_all:
        return True
    if client_host in literals:
        return True
    try:
        ip = ip_address(client_host)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def _get_header_value(
    headers: Sequence[tuple[bytes, bytes]], name: bytes
) -> str | None:
    for key, value in headers:
        if key == name:
            return value.decode("latin-1")
    return None


class TrustedProxyHeadersMiddleware:
    def __init__(self, app: ASGIApp, trusted_hosts: list[str]) -> None:
        self.app = app
        self._trust_all, self._trusted_networks, self._trusted_literals = (
            _normalize_trusted_hosts(trusted_hosts)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = client[0] if client else None
        if client_host and _is_trusted_host(
            client_host,
            trust_all=self._trust_all,
            networks=self._trusted_networks,
            literals=self._trusted_literals,
        ):
            headers = scope.get("headers") or []
            forwarded_proto = _get_header_value(headers, b"x-forwarded-proto")
            if forwarded_proto:
                scheme = forwarded_proto.split(",")[0].strip()
                if scheme:
                    scope["scheme"] = scheme

        await self.app(scope, receive, send)
