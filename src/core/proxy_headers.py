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

# Schemes a forwarding proxy may legitimately report. Anything else would end up
# in scope["scheme"] and from there in redirect Location headers.
ALLOWED_FORWARDED_SCHEMES = frozenset({"http", "https", "ws", "wss"})


def _normalize_trusted_hosts(
    values: Sequence[str],
) -> tuple[list[TrustedNetwork], set[str]]:
    """
    Split the configured entries into networks and non-address literals.

    There is deliberately no wildcard: trusting every hop leaves no honest end
    of the forwarded chain to start from, so the caller would be free to pick
    their own address. `AppConfig` rejects "*" outright, and anything that
    reaches here and is not an address ends up as a literal that no real hop
    can match - fail-closed either way.
    """
    networks: list[TrustedNetwork] = []
    literals: set[str] = set()

    for raw_value in values:
        value = raw_value.strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
            continue
        except ValueError:
            literals.add(value)

    return networks, literals


def _is_trusted_host(
    client_host: str,
    networks: Sequence[TrustedNetwork],
    literals: set[str],
) -> bool:
    if client_host in literals:
        return True
    try:
        ip = ip_address(client_host)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def parse_forwarded_hop(value: str) -> str | None:
    """
    Turn one forwarded-chain entry into a normalized IP address.

    Proxies write hops in several shapes - a bare address, `address:port`, and
    IPv6 either bare or bracketed with an optional port. All of them have to
    collapse onto the same string, or the same client lands in two different
    rate-limit buckets depending on which proxy wrote the entry. Returns None
    for anything that is not an address, `unknown` and obfuscated ids included.
    """
    host = value.strip()
    if not host:
        return None

    if host.startswith("["):
        closing = host.find("]")
        if closing == -1:
            return None
        host = host[1:closing]
    elif host.count(":") == 1:
        # A bare IPv6 address always carries more than one colon, so a single
        # one can only be the port separator of an IPv4 entry.
        host = host.split(":", 1)[0]

    try:
        return str(ip_address(host))
    except ValueError:
        return None


def _get_header_value(
    headers: Sequence[tuple[bytes, bytes]], name: bytes
) -> str | None:
    """
    Join every occurrence of a header in the order it was received.

    HTTP allows one header to arrive as several field lines, and some proxies
    (HAProxy's `option forwardfor`, a few ingress controllers) append their hop
    as a new line instead of extending the existing one. Reading only the first
    line would hand us a chain the client wrote in full.
    """
    values = [value.decode("latin-1") for key, value in headers if key == name]
    if not values:
        return None
    return ",".join(values)


class TrustedProxyHeadersMiddleware:
    """
    Single source of truth for what the client address and scheme really are.

    Everything downstream — the rate limiter above all — reads `scope["client"]`
    and trusts it, so the forwarded chain is resolved exactly once, here, and
    only for a request that arrived from a proxy listed in `TRUST_PROXY_HOSTS`.
    """

    def __init__(self, app: ASGIApp, trusted_hosts: list[str]) -> None:
        self.app = app
        self._trusted_networks, self._trusted_literals = _normalize_trusted_hosts(
            trusted_hosts
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
                scheme = forwarded_proto.split(",")[0].strip().lower()
                if scheme in ALLOWED_FORWARDED_SCHEMES:
                    scope["scheme"] = scheme

            forwarded_for = _get_header_value(headers, b"x-forwarded-for")
            if forwarded_for:
                resolved = self._resolve_client_host(forwarded_for)
                if resolved:
                    # Port 0: the peer port belongs to the proxy connection and
                    # pairing it with the client address would invent an
                    # endpoint that never existed.
                    scope["client"] = (resolved, 0)

        await self.app(scope, receive, send)

    def _is_trusted(self, host: str) -> bool:
        return _is_trusted_host(
            host,
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
        hops: list[str] = []
        for raw_hop in forwarded_for.split(","):
            if not raw_hop.strip():
                continue
            hop = parse_forwarded_hop(raw_hop)
            if hop is None:
                return None
            hops.append(hop)

        if not hops:
            return None

        for hop in reversed(hops):
            if not self._is_trusted(hop):
                return hop

        # Every hop is trusted: the client itself sits inside the trusted range.
        return hops[0]
