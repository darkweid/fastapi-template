from ipaddress import ip_address

from fastapi import Request


def get_client_ip(request: Request) -> str | None:
    """The peer address as the proxy middleware left it, or None.

    Never reads `X-Forwarded-For`: `TrustedProxyHeadersMiddleware` is the only
    code allowed to trust that header, and it has already rewritten
    `scope["client"]` by the time a dependency runs.

    Validated through `ipaddress`, so callers that must hand the value to
    something typed - an `INET` column, an allowlist check - get either a
    normalized address or nothing, never an arbitrary string. An ASGI server
    is free to put a hostname or a unix socket path in `scope["client"]`.
    """
    if request.client is None:
        return None
    try:
        return str(ip_address(request.client.host))
    except ValueError:
        return None
