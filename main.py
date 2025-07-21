import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_pagination import add_pagination
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from app.core.middleware import register_middlewares
from app.core.routes import v1
from app.core.settings import settings
from loggers import get_logger

logging.getLogger("uvicorn.access").disabled = True
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    pass
    try:
        logger.info("Lifespan started")
        # await redis_client.ping()
        # logger.info("Successfully connected to Redis")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise

    yield
    logger.info("Lifespan ended")
    # await redis_client.aclose()
    # logger.info("Redis connection closed")


def get_application() -> FastAPI:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_env,
        traces_sample_rate=1.0,
        _experiments={
            "continuous_profiling_auto_start": True,
        },
    )

    application = FastAPI(
        title=settings.project_name,
        debug=settings.debug,
        version=settings.version,
        lifespan=lifespan,
    )

    application.include_router(v1, prefix="/api/v1")
    logger.info(f"Total endpoints: %s", len(application.routes) - 4)

    # Register middlewares from core/middleware.py
    register_middlewares(application)

    # CORS
    application.add_middleware(
        CORSMiddleware,  # noqa
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )

    # Sentry middleware for error tracking
    application.add_middleware(SentryAsgiMiddleware)  # noqa

    add_pagination(application)

    return application


app = get_application()
