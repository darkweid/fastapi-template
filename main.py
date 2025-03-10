import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_pagination import add_pagination
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from app.core.database_async import init_models
from app.core.middleware import ValidationErrorMiddleware, UnexpectedErrorMiddleware, DatabaseErrorMiddleware
from app.core.routes import v1
from app.core.settings import settings
from loggers import get_logger

logger = get_logger(__name__)


def get_application() -> FastAPI:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for tracing.
        traces_sample_rate=1.0,
        _experiments={
            # Set continuous_profiling_auto_start to True
            # to automatically start the profiler on when
            # possible.
            "continuous_profiling_auto_start": True,
        },
    )
    application = FastAPI(
        title=settings.project_name,
        debug=settings.debug,
        version=settings.version,
    )

    application.include_router(v1, prefix="/api/v1")
    logger.info(f"Total endpoints: %s", len(application.routes))

    application.add_middleware(
        CORSMiddleware,  # noqa
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Sentry middleware for error tracking
    application.add_middleware(SentryAsgiMiddleware)  # noqa
    application.add_middleware(ValidationErrorMiddleware)  # noqa
    application.add_middleware(DatabaseErrorMiddleware)  # noqa
    application.add_middleware(UnexpectedErrorMiddleware)  # noqa

    add_pagination(application)

    @application.on_event("startup")
    async def startup_event():
        await init_models()

        from app.core.redis import redis_client
        try:
            await redis_client.ping()
            logger.info("Successfully connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise e

    @application.on_event("shutdown")
    async def shutdown_event():
        from app.core.redis import redis_client
        await redis_client.close()

    return application


app = get_application()
