import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from loggers import get_logger
from src.core.middleware import register_middlewares
from src.core.proxy_headers import TrustedProxyHeadersMiddleware
from src.main.config import config
from src.main.docs import (
    DOCS_URL,
    OPENAPI_URL,
    REDOC_URL,
    docs_are_protected,
    include_protected_docs,
)
from src.main.lifespan import lifespan
from src.main.openapi import SWAGGER_UI_PARAMETERS
from src.main.presentation import include_exceptions_handlers, include_routers
from src.main.route_logging import log_routes_summary

logging.getLogger("uvicorn.access").disabled = True
logger = get_logger(__name__)


def get_application() -> FastAPI:
    docs_are_public = config.app.DEBUG
    application = FastAPI(
        title=config.app.PROJECT_NAME,
        debug=config.app.DEBUG,
        version=config.app.VERSION,
        lifespan=lifespan,
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
        docs_url=DOCS_URL if docs_are_public else None,
        redoc_url=REDOC_URL if docs_are_public else None,
        openapi_url=OPENAPI_URL if docs_are_public else None,
    )

    if not docs_are_public:
        if docs_are_protected():
            include_protected_docs(application)
        else:
            logger.warning(
                "Interactive docs are not published: set DOCS_USERNAME and "
                "DOCS_PASSWORD to serve them behind HTTP Basic."
            )

    if config.app.TRUST_PROXY_HEADERS:
        application.add_middleware(
            TrustedProxyHeadersMiddleware,
            trusted_hosts=config.app.TRUST_PROXY_HOSTS,
        )

    # Register custom middlewares
    register_middlewares(application)

    # CORS
    application.add_middleware(
        CORSMiddleware,  # noqa
        allow_origins=config.app.CORS_ALLOWED_ORIGINS,
        allow_credentials=config.app.CORS_ALLOWED_CREDENTIALS,
        allow_methods=config.app.CORS_ALLOWED_METHODS,
        allow_headers=config.app.CORS_ALLOWED_HEADERS,
        expose_headers=config.app.CORS_EXPOSE_HEADERS,
    )

    # Custom exceptions
    include_exceptions_handlers(application)

    # Routers
    include_routers(application)
    log_routes_summary(application, include_debug_list=config.app.DEBUG)

    # Sentry middleware for error tracking
    application.add_middleware(SentryAsgiMiddleware)

    return application


app = get_application()
