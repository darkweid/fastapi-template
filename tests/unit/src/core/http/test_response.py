from multidict import CIMultiDict, CIMultiDictProxy
import pytest

from src.core.http.errors import HttpTransportError
from src.core.http.response import HttpResponse
from src.core.http.status import is_permanent_failure


def _response(status: int, body: bytes) -> HttpResponse:
    return HttpResponse(
        status=status, headers=CIMultiDictProxy(CIMultiDict()), body=body
    )


def test_json_reads_the_body() -> None:
    assert _response(200, b'{"a": 1}').json() == {"a": 1}


def test_an_unreadable_body_is_none_for_the_lenient_reader() -> None:
    """A proxy's HTML error page must not hide the status that came with it."""
    response = _response(502, b"<html>bad gateway</html>")

    assert response.json_or_none() is None
    with pytest.raises(ValueError):
        response.json()


def test_invalid_utf8_is_none_too() -> None:
    assert _response(200, b"\xff\xfe\xfa").json_or_none() is None


@pytest.mark.parametrize(
    ("status", "ok"), [(199, False), (200, True), (299, True), (300, False)]
)
def test_ok_means_2xx(status: int, ok: bool) -> None:
    assert _response(status, b"").ok is ok


@pytest.mark.parametrize(
    ("status", "permanent"),
    [
        (400, True),
        (401, True),
        (404, True),
        (408, False),
        (429, False),
        (500, False),
        (200, False),
    ],
)
def test_only_a_4xx_other_than_timeout_or_rate_limit_is_permanent(
    status: int, permanent: bool
) -> None:
    assert is_permanent_failure(status) is permanent


def test_a_transport_error_names_the_operation_and_the_class_only() -> None:
    error = HttpTransportError(
        "telegram.send", "ClientConnectorError", request_sent=False, transient=True
    )

    assert error.message == "telegram.send: ClientConnectorError"
    assert (error.request_sent, error.transient) == (False, True)
