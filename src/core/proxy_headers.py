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


def _is_ip_address(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _get_header_value(
    headers: Sequence[tuple[bytes, bytes]], name: bytes
) -> str | None:
    for key, value in headers:
        if key == name:
            return value.decode("latin-1")
    return None


class TrustedProxyHeadersMiddleware:
    """
    Single source of truth for what the client address and scheme really are.

    Everything downstream — the rate limiter above all — reads `scope["client"]`
    and trusts it, so the forwarded chain is resolved exactly once, here, and
    only for a request that arrived from a proxy listed in `TRUST_PROXY_HOSTS`.
    """

    def __init__(self, app: ASGIApp, trusted_hosts: list[str]) -> None:
        self.app = app
        self._trust_all, self._trusted_networks, self._trusted_literals = (
            _normalize_trusted_hosts(trusted_hosts)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client: tuple[str, int] | None = scope.get("client")
        if client and self._is_trusted(client[0]):
            headers = scope.get("headers") or []
            forwarded_proto = _get_header_value(headers, b"x-forwarded-proto")
            if forwarded_proto:
                scheme = forwarded_proto.split(",")[0].strip()
                if scheme:
                    scope["scheme"] = scheme

            forwarded_for = _get_header_value(headers, b"x-forwarded-for")
            if forwarded_for:
                resolved = self._resolve_client_host(forwarded_for)
                if resolved:
                    scope["client"] = (resolved, client[1])

        await self.app(scope, receive, send)

    def _is_trusted(self, host: str) -> bool:
        return _is_trusted_host(
            host,
            trust_all=self._trust_all,
            networks=self._trusted_networks,
            literals=self._trusted_literals,
        )

    def _resolve_client_host(self, forwarded_for: str) -> str | None:
        """
        Walk the forwarded chain right to left, dropping trusted hops.

        The rightmost entry is the one our own proxy appended, so it is the only
        end of the chain a client cannot write. Anything left of the first
        untrusted hop is attacker-controlled and must never be read. Returns
        None when the chain yields no usable address, which leaves the direct
        peer in place — a shared bucket is a safe failure, a forged one is not.
        """
        hops = [hop.strip() for hop in forwarded_for.split(",")]
        hops = [hop for hop in hops if hop]
        if not hops:
            return None

        for hop in reversed(hops):
            if self._is_trusted(hop):
                continue
            return hop if _is_ip_address(hop) else None

        # Every hop is trusted: the client itself sits inside the trusted range.
        leftmost = hops[0]
        return leftmost if _is_ip_address(leftmost) else None
