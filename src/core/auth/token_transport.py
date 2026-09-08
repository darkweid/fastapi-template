from enum import StrEnum
from typing import Annotated

from fastapi import Header


class TokenTransport(StrEnum):
    """
    How the refresh token is delivered to the client.

    This decides the shape of the response only. It never decides how an incoming
    refresh token is read: a client must not be able to opt out of the CSRF check
    by declaring a transport.
    """

    COOKIE = "cookie"
    BODY = "body"


async def get_token_transport(
    x_token_transport: Annotated[TokenTransport | None, Header()] = None,
) -> TokenTransport:
    """
    Resolve the response transport from the X-Token-Transport header.

    Defaults to COOKIE. A native client that forgets the header fails loudly at
    integration time; the reverse default would silently downgrade browser security.
    An unknown value is rejected by FastAPI validation with 422.
    """
    return x_token_transport or TokenTransport.COOKIE
