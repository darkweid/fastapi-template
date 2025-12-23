from functools import lru_cache
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


class AWSConfig(BaseModel):
    BUCKET_NAME: str
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    REGION_NAME: str
    S3_SAMPLE_URL: str
    PRE_SIGNED_URL_SECONDS: int = Field(300, gt=0)

    model_config = ConfigDict(extra="ignore")


class BroadcastingConfig(BaseModel):
    EMAIL_SERVER: str
    EMAIL_PORT: int
    EMAIL_PASSWORD: str
    EMAIL_USER: str
    EMAIL_FROM_NAME: str
    EMAIL_USE_TLS: bool
    EMAIL_STARTTLS: bool
    VALIDATE_CERTS: bool

    model_config = ConfigDict(extra="ignore")


class RedisConfig(BaseModel):
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: str
    REDIS_DATABASE: str
    REDIS_CELERY_DATABASE: str = "1"

    model_config = ConfigDict(extra="ignore")

    @property
    def dsn(self) -> str:
        return (
            f"redis://:"
            f"{self.REDIS_PASSWORD}@"
            f"{self.REDIS_HOST}:"
            f"{self.REDIS_PORT}/"
            f"{self.REDIS_DATABASE}"
        )

    @property
    def celery_dsn(self) -> str:
        return (
            f"redis://:"
            f"{self.REDIS_PASSWORD}@"
            f"{self.REDIS_HOST}:"
            f"{self.REDIS_PORT}/"
            f"{self.REDIS_CELERY_DATABASE}"
        )


class RabbitMQConfig(BaseModel):
    RABBITMQ_HOST: str
    RABBITMQ_PORT: int
    RABBITMQ_USER: str
    RABBITMQ_PASSWORD: str

    model_config = ConfigDict(extra="ignore")

    @property
    def dsn(self) -> str:
        return (
            f"amqp://"
            f"{self.RABBITMQ_USER}:"
            f"{self.RABBITMQ_PASSWORD}@"
            f"{self.RABBITMQ_HOST}:"
            f"{self.RABBITMQ_PORT}//"
        )


class SentryConfig(BaseModel):
    SENTRY_DSN: str | None = None
    SENTRY_ENV: str = "development"
    SENTRY_ENABLED: bool = False

    model_config = ConfigDict(extra="ignore")


class JWTConfig(BaseModel):
    JWT_USER_SECRET_KEY: str
    JWT_VERIFY_SECRET_KEY: str
    JWT_ADMIN_SECRET_KEY: str
    JWT_RESET_PASSWORD_SECRET_KEY: str

    ALGORITHM: str

    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(gt=0)
    REFRESH_TOKEN_EXPIRE_MINUTES: int = Field(gt=0)
    REFRESH_TOKEN_USED_TTL_SECONDS: int = Field(1_209_600, gt=0)
    VERIFICATION_TOKEN_EXPIRE_MINUTES: int = Field(gt=0)
    RESET_PASSWORD_TOKEN_EXPIRE_MINUTES: int = Field(gt=0)

    model_config = ConfigDict(extra="ignore")


class PostgresConfig(BaseModel):
    DB_ECHO: bool

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_DB: str

    model_config = ConfigDict(extra="ignore")

    @property
    def dsn_async(self) -> str:
        return (
            f"postgresql+asyncpg://"
            f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )

    @property
    def dsn_sync(self) -> str:
        return (
            f"postgresql://"
            f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )


class AdministrationConfig(BaseModel):
    SUPER_ADMIN_USERNAME: str
    SUPER_ADMIN_PASSWORD: str
    SUPER_ADMIN_EMAIL: str
    SUPER_ADMIN_PHONE: str

    model_config = ConfigDict(extra="ignore")


class AppConfig(BaseModel):
    VERSION: str
    DEBUG: bool = False
    TESTING: bool = False

    LOCAL_TIMEZONE: str

    LOG_LEVEL: str
    LOG_LEVEL_FILE: str

    CORS_ALLOWED_ORIGINS: list[str] = Field(["*"])
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOWED_METHODS: list[str] = Field(["*"])
    CORS_ALLOWED_HEADERS: list[str] = Field(["*"])
    CORS_EXPOSE_HEADERS: list[str] = Field(["*"])

    TRUST_PROXY_HEADERS: bool

    PROJECT_NAME: str
    PROJECT_SECRET_KEY: str

    PING_INTERVAL: int
    CONNECTION_TTL: int

    model_config = ConfigDict(extra="ignore")

    @field_validator(
        "CORS_ALLOWED_ORIGINS",
        "CORS_ALLOWED_METHODS",
        "CORS_ALLOWED_HEADERS",
        "CORS_EXPOSE_HEADERS",
        mode="before",
    )
    @classmethod
    def parse_cors_list(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str) and v.strip().startswith("[") and v.strip().endswith("]"):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except json.JSONDecodeError:
                pass
        sep = "," if "," in v else ";"
        return [item.strip() for item in v.split(sep) if item.strip()]


class Config(BaseModel):
    _project_root: Path | None = None

    app: AppConfig
    aws: AWSConfig
    jwt: JWTConfig
    redis: RedisConfig
    sentry: SentryConfig
    postgres: PostgresConfig
    rabbitmq: RabbitMQConfig
    broadcasting: BroadcastingConfig
    administration: AdministrationConfig

    model_config = ConfigDict(extra="ignore")

    @property
    def project_root(self) -> Path:
        if self._project_root is None:
            self._project_root = find_project_root_robust()
        return self._project_root


@lru_cache
def get_settings() -> Config:
    """
    Cached settings factory. Override in tests via monkeypatching or dependency overrides.
    """
    env_filename = ".env.test" if os.getenv("TESTING") == "true" else ".env"
    env_file_values = dotenv_values(env_filename)
    merged_env: dict[str, Any] = {
        k: v
        for k, v in {**env_file_values, **dict(os.environ)}.items()
        if v is not None
    }

    return Config(
        app=AppConfig(**merged_env),
        aws=AWSConfig(**merged_env),
        jwt=JWTConfig(**merged_env),
        redis=RedisConfig(**merged_env),
        sentry=SentryConfig(**merged_env),
        postgres=PostgresConfig(**merged_env),
        rabbitmq=RabbitMQConfig(**merged_env),
        broadcasting=BroadcastingConfig(**merged_env),
        administration=AdministrationConfig(**merged_env),
    )


config = get_settings()


# ----- Config utils ----- #
def find_project_root_robust(
    start_path: Path | None = None, max_depth: int = 10
) -> Path:
    """
    A more robust version of find_project_root with configurable parameters.

    Args:
        start_path: Starting path to search from (defaults to current working directory)
        max_depth: Maximum number of parent directories to traverse

    Returns:
        Path: The project root directory if found, otherwise the starting path
    """
    if start_path is None:
        start_path = Path.cwd()

    markers = {
        ".git": 100,
        "pyproject.toml": 90,
        "setup.py": 80,
        "setup.cfg": 75,
        "requirements": 70,
        "Pipfile": 70,
        "poetry.lock": 70,
        "README.md": 50,
        "Makefile": 60,
    }

    best_match = None
    best_score = 0

    current_path = start_path
    depth = 0

    while current_path != current_path.parent and depth < max_depth:
        score = 0
        for marker, weight in markers.items():
            if (current_path / marker).exists():
                score += weight

        if score > best_score:
            best_score = score
            best_match = current_path

        current_path = current_path.parent
        depth += 1

    if best_match and best_score > 0:
        logger.info(
            f"Project root found: {best_match} (confidence score: {best_score})"
        )
        return best_match

    logger.error(
        "No project root found within %s parent directories from %s",
        max_depth,
        start_path,
    )
    return start_path
