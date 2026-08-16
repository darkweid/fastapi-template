import uuid

import pytest

from src.core.request_context import get_request_id, request_id_var, resolve_request_id


def test_resolve_request_id_echoes_valid_inbound_id() -> None:
    assert resolve_request_id("abc-123-XYZ") == "abc-123-XYZ"


def test_resolve_request_id_generates_when_inbound_is_none() -> None:
    resolved = resolve_request_id(None)
    assert uuid.UUID(hex=resolved)


def test_resolve_request_id_generates_when_inbound_is_empty() -> None:
    resolved = resolve_request_id("")
    assert uuid.UUID(hex=resolved)


def test_resolve_request_id_generates_when_inbound_is_overlong() -> None:
    resolved = resolve_request_id("a" * 65)
    assert uuid.UUID(hex=resolved)


def test_resolve_request_id_accepts_max_length_id() -> None:
    max_length_id = "a" * 64
    assert resolve_request_id(max_length_id) == max_length_id


@pytest.mark.parametrize(
    "inbound",
    [
        "id with spaces",
        "id\r\nInjected: header",
        "id/with/slashes",
        "id;with;semicolons",
        "id\twith\ttab",
        "id_with_underscore",
        "id-with-trailing-newline\n",
    ],
)
def test_resolve_request_id_generates_when_inbound_has_unsafe_characters(
    inbound: str,
) -> None:
    resolved = resolve_request_id(inbound)
    assert uuid.UUID(hex=resolved)
    assert resolved != inbound


def test_resolve_request_id_rejects_trailing_newline() -> None:
    # Regression: re's $ matches just before a trailing "\n", so match()
    # (unlike fullmatch()) would accept "valid-id\n" and let the newline
    # reach logs/headers. Named separately from the parametrized case above
    # to pin this exact regression.
    resolved = resolve_request_id("valid-id-123\n")
    assert resolved != "valid-id-123\n"
    assert uuid.UUID(hex=resolved)


def test_get_request_id_returns_none_outside_request_context() -> None:
    assert get_request_id() is None


def test_get_request_id_reflects_contextvar() -> None:
    token = request_id_var.set("some-id")
    try:
        assert get_request_id() == "some-id"
    finally:
        request_id_var.reset(token)
