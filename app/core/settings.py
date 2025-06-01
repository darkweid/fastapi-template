from pydantic_settings import BaseSettings
from sqlalchemy import URL
from pytz import timezone


class Settings(BaseSettings):
    timezone: str = "Asia/Tashkent"
    sentry_dsn: str
    sentry_env: str = "development"
    db_echo: bool
    project_name: str
    version: str
    debug: bool
    cors_allowed_origins: str

    log_level: str
    log_level_file: str

    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    postgres_db: str

    jwt_user_secret_key: str
    jwt_admin_secret_key: str
    algorithm: str
    access_token_expire_minutes: int
    refresh_token_expire_minutes: int

    redis_host: str
    redis_port: int
    redis_password: str
    redis_database: int = 0

    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_password: str

    bucket_name: str
    aws_access_key_id: str
    aws_secret_access_key: str
    region_name: str
    s3_sample_url: str

    @property
    def tz(self):
        """Return timezone-aware object."""
        return timezone(self.timezone)

    def build_postgres_dsn_async(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    def build_postgres_dsn_sync(self) -> URL:
        return URL.create(
            "postgresql",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    def build_redis_dsn(self) -> str:
        return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_database}"

    def build_rabbitmq_dsn(self) -> str:
        return f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}@{self.rabbitmq_host}:{self.rabbitmq_port}//"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
