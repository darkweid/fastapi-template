from functools import lru_cache

from src.core.http.user_agent import build_user_agent
from src.main.config import config


@lru_cache(maxsize=1)
def get_user_agent() -> str:
    return config.http.HTTP_USER_AGENT or build_user_agent(
        config.app.PROJECT_NAME, config.app.VERSION
    )
