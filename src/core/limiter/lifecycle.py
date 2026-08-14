from loggers import get_logger
from src.core.limiter import FastAPILimiter

logger = get_logger(__name__)


async def on_limiter_startup(connection_url: str) -> None:
    await FastAPILimiter.init(connection_url)
    logger.info("Rate limiter started successfully.")


async def on_limiter_shutdown() -> None:
    await FastAPILimiter.close()
    logger.info("Rate limiter stopped.")
