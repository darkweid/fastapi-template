import json
import logging
from os import environ
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

logger = logging.getLogger(__name__)


class AWSConfig(BaseModel):
    BUCKET_NAME: str = Field(alias="BUCKET_NAME")
    AWS_ACCESS_KEY_ID: str = Field(alias="AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: str = Field(alias="AWS_SECRET_ACCESS_KEY")
    REGION_NAME: str = Field(alias="REGION_NAME")
    S3_SAMPLE_URL: str = Field(alias="S3_SAMPLE_URL")
    PRE_SIGNED_URL_SECONDS: str = Field(alias="PRE_SIGNED_URL_SECONDS")

    model_config = ConfigDict(extra="ignore")


class BroadcastingConfig(BaseModel):
    EMAIL_SERVER: str = Field(alias="EMAIL_SERVER")
    EMAIL_PORT: int = Field(alias="EMAIL_PORT")
    EMAIL_PASSWORD: str = Field(alias="EMAIL_PASSWORD")
    EMAIL_USER: str = Field(alias="EMAIL_USER")
    EMAIL_FROM_NAME: str = Field(alias="EMAIL_FROM_NAME")
    EMAIL_USE_TLS: bool = Field(alias="EMAIL_USE_TLS")
    EMAIL_STARTTLS: bool = Field(alias="EMAIL_STARTTLS")
    VALIDATE_CERTS: bool = Field(alias="VALIDATE_CERTS")

    model_config = ConfigDict(extra="ignore")


class RedisConfig(BaseModel):
    REDIS_HOST: str = Field(alias="REDIS_HOST")
    REDIS_PORT: int = Field(alias="REDIS_PORT")
    REDIS_PASSWORD: str = Field(alias="REDIS_PASSWORD")
    REDIS_DATABASE: str = Field(alias="REDIS_DATABASE")
    REDIS_CELERY_DATABASE: str = Field("1", alias="REDIS_CELERY_DATABASE")

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
    RABBITMQ_HOST: str = Field(alias="RABBITMQ_HOST")
    RABBITMQ_PORT: int = Field(alias="RABBITMQ_PORT")
    RABBITMQ_USER: str = Field(alias="RABBITMQ_USER")
    RABBITMQ_PASSWORD: str = Field(alias="RABBITMQ_PASSWORD")

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
    SENTRY_DSN: str | None = Field(None, alias="SENTRY_DSN")
    SENTRY_ENV: str = Field("development", alias="SENTRY_ENV")

    model_config = ConfigDict(extra="ignore")


class JWTConfig(BaseModel):
    JWT_USER_SECRET_KEY: str = Field(alias="JWT_USER_SECRET_KEY")
    JWT_VERIFY_SECRET_KEY: str = Field(alias="JWT_VERIFY_SECRET_KEY")
    JWT_ADMIN_SECRET_KEY: str = Field(alias="JWT_ADMIN_SECRET_KEY")
    JWT_RESET_PASSWORD_SECRET_KEY: str = Field(alias="JWT_RESET_PASSWORD_SECRET_KEY")

    ALGORITHM: str = Field(alias="ALGORITHM")

    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    REFRESH_TOKEN_EXPIRE_MINUTES: int = Field(alias="REFRESH_TOKEN_EXPIRE_MINUTES")

    model_config = ConfigDict(extra="ignore")


class PostgresConfig(BaseModel):
    DB_ECHO: bool = Field(alias="DB_ECHO")

    POSTGRES_USER: str = Field(alias="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field(alias="POSTGRES_PASSWORD")
    POSTGRES_HOST: str = Field(alias="POSTGRES_HOST")
    POSTGRES_PORT: int = Field(alias="POSTGRES_PORT")
    POSTGRES_DB: str = Field(alias="POSTGRES_DB")

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
    SUPER_ADMIN_USERNAME: str = Field(alias="SUPER_ADMIN_USERNAME")
    SUPER_ADMIN_PASSWORD: str = Field(alias="SUPER_ADMIN_PASSWORD")
    SUPER_ADMIN_EMAIL: str = Field(alias="SUPER_ADMIN_EMAIL")
    SUPER_ADMIN_PHONE: str = Field(alias="SUPER_ADMIN_PHONE")

    model_config = ConfigDict(extra="ignore")


class AppConfig(BaseModel):
    VERSION: str = Field(alias="VERSION")
    DEBUG: bool = Field(False, alias="DEBUG")

    LOCAL_TIMEZONE: str = Field(alias="LOCAL_TIMEZONE")

    LOG_LEVEL: str = Field(alias="LOG_LEVEL")
    LOG_LEVEL_FILE: str = Field(alias="LOG_LEVEL_FILE")

    CORS_ALLOWED_ORIGINS: list[str] = Field(["*"], alias="CORS_ALLOWED_ORIGINS")
    CORS_ALLOW_CREDENTIALS: bool = Field(True, alias="CORS_ALLOW_CREDENTIALS")
    CORS_ALLOWED_METHODS: list[str] = Field(["*"], alias="CORS_ALLOWED_METHODS")
    CORS_ALLOWED_HEADERS: list[str] = Field(["*"], alias="CORS_ALLOWED_HEADERS")
    CORS_EXPOSE_HEADERS: list[str] = Field(["*"], alias="CORS_EXPOSE_HEADERS")

    TRUST_PROXY_HEADERS: str = Field(alias="TRUST_PROXY_HEADERS")

    PROJECT_NAME: str = Field(alias="PROJECT_NAME")
    PROJECT_SECRET_KEY: str = Field(alias="PROJECT_SECRET_KEY")

    PING_INTERVAL: int = Field(alias="PING_INTERVAL")
    CONNECTION_TTL: int = Field(alias="CONNECTION_TTL")

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


class Config(BaseSettings):
    _project_root: Path | None = None

    app: AppConfig = Field(default_factory=lambda: AppConfig(**environ))
    aws: AWSConfig = Field(default_factory=lambda: AWSConfig(**environ))
    jwt: JWTConfig = Field(default_factory=lambda: JWTConfig(**environ))
    redis: RedisConfig = Field(default_factory=lambda: RedisConfig(**environ))
    sentry: SentryConfig = Field(default_factory=lambda: SentryConfig(**environ))
    postgres: PostgresConfig = Field(default_factory=lambda: PostgresConfig(**environ))
    rabbitmq: RabbitMQConfig = Field(default_factory=lambda: RabbitMQConfig(**environ))
    broadcasting: BroadcastingConfig = Field(
        default_factory=lambda: BroadcastingConfig(**environ)
    )
    administration: AdministrationConfig = Field(
        default_factory=lambda: AdministrationConfig(**environ)
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        env_nested_delimiter="__",
    )

    @property
    def project_root(self) -> Path:
        if self._project_root is None:
            self._project_root = find_project_root_robust()
        return self._project_root


config = Config()


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
        "requirements.txt": 70,
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
