"""Interactive API docs, served behind HTTP Basic outside DEBUG.

The docs are a full map of every endpoint and payload shape, so they are open
only where that map costs nothing (local development) and behind credentials
everywhere else. Closing them entirely is deliberately not the answer: the team
running the service needs them more than an attacker does.
"""

from secrets import compare_digest
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config
from src.main.openapi import SWAGGER_UI_PARAMETERS

DOCS_URL = "/docs"
REDOC_URL = "/redoc"
OPENAPI_URL = "/openapi.json"

_BASIC_CHALLENGE = 'Basic realm="API docs"'

# auto_error=False so a missing header reaches verify_docs_access and gets the
# project's own 401 body plus the challenge header, instead of Starlette's.
docs_basic_auth = HTTPBasic(auto_error=False)


def docs_are_protected() -> bool:
    """Whether the credentials needed to publish protected docs are configured."""
    return bool(config.app.DOCS_USERNAME and config.app.DOCS_PASSWORD)


async def verify_docs_access(
    credentials: Annotated[
        HTTPBasicCredentials | None, Depends(docs_basic_auth)
    ] = None,
) -> None:
    if credentials is None:
        raise UnauthorizedException(
            message="Docs require authentication.",
            www_authenticate=_BASIC_CHALLENGE,
        )

    username_matches = compare_digest(
        credentials.username.encode("utf-8"),
        config.app.DOCS_USERNAME.encode("utf-8"),
    )
    password_matches = compare_digest(
        credentials.password.encode("utf-8"),
        config.app.DOCS_PASSWORD.encode("utf-8"),
    )
    if not (username_matches and password_matches):
        raise UnauthorizedException(
            message="Invalid docs credentials.",
            www_authenticate=_BASIC_CHALLENGE,
        )


async def openapi_schema(
    request: Request,
    _: Annotated[None, Depends(verify_docs_access)],
) -> JSONResponse:
    schema: dict[str, Any] = request.app.openapi()
    return JSONResponse(schema)


async def swagger_ui(
    request: Request,
    _: Annotated[None, Depends(verify_docs_access)],
) -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=OPENAPI_URL,
        title=f"{request.app.title} - Swagger UI",
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    )


async def redoc_ui(
    request: Request,
    _: Annotated[None, Depends(verify_docs_access)],
) -> HTMLResponse:
    return get_redoc_html(
        openapi_url=OPENAPI_URL,
        title=f"{request.app.title} - ReDoc",
    )


def include_protected_docs(application: FastAPI) -> None:
    """
    Mount /docs, /redoc and /openapi.json behind the Basic credentials.

    Called only when FastAPI was built with all three URLs set to None, so these
    routes are the single source of the docs rather than a second copy of them.
    """
    application.add_api_route(
        OPENAPI_URL, openapi_schema, include_in_schema=False, methods=["GET"]
    )
    application.add_api_route(
        DOCS_URL, swagger_ui, include_in_schema=False, methods=["GET"]
    )
    application.add_api_route(
        REDOC_URL, redoc_ui, include_in_schema=False, methods=["GET"]
    )
