from types import SimpleNamespace

from src.core.request_ip import get_client_ip


def test_the_peer_address_is_what_is_read() -> None:
    """TrustedProxyHeadersMiddleware has already rewritten scope['client'];
    reading the header here would undo that and trust the caller."""
    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"))

    assert get_client_ip(request) == "10.0.0.1"


def test_a_request_without_a_peer_yields_none() -> None:
    """ASGI allows scope['client'] to be absent."""
    assert get_client_ip(SimpleNamespace(client=None)) is None


def test_an_ipv6_peer_address_survives() -> None:
    """A real IPv6 peer must not be discarded along with the malformed ones."""
    request = SimpleNamespace(client=SimpleNamespace(host="2001:db8::1"))

    assert get_client_ip(request) == "2001:db8::1"


def test_a_non_address_host_yields_none() -> None:
    """A hostname or a socket path is not an address a typed consumer can take."""
    request = SimpleNamespace(client=SimpleNamespace(host="not-an-ip"))

    assert get_client_ip(request) is None
