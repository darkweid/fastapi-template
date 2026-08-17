from functools import lru_cache
import json
import os
from typing import Any, Literal
from urllib.parse import quote, urlparse

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Shortest secret the app accepts anywhere. Matches the HMAC-SHA256 block size,
# which is the weakest signature the JWT algorithm allowlist permits.
SECRET_MIN_LENGTH = 32


class S3Config(BaseModel):
    S3_ENABLED: bool = False
    S3_BUCKET_NAME: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_REGION_NAME: str | None = None
    S3_PRE_SIGNED_URL_SECONDS: int = Field(300, gt=0)
    S3_ENDPOINT_URL: str | None = None
    S3_ADDRESSING_STYLE: str | None = None
    S3_SIGNATURE_VERSION: str = "s3v4"
    S3_VERIFY_SSL: bool = True
    S3_CA_BUNDLE: str | None = None
    S3_TREAT_ACCESS_DENIED_AS_MISSING: bool = False
    S3_CONNECT_TIMEOUT_SECONDS: int = Field(5, gt=0)
    S3_READ_TIMEOUT_SECONDS: int = Field(120, gt=0)
    S3_RETRY_MAX_ATTEMPTS: int = Field(3, gt=0)
    S3_RETRY_MODE: str = "standard"
    S3_MAX_UPLOAD_SIZE_BYTES: int = Field(20 * 1024 * 1024, gt=0)

    model_config = ConfigDict(extra="ignore")

    @field_validator(
        "S3_ENDPOINT_URL",
        "S3_ADDRESSING_STYLE",
        "S3_CA_BUNDLE",
        mode="before",
    )
    @classmethod
    def normalize_optional_fields(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        return str(value)

    @field_validator("S3_SIGNATURE_VERSION", "S3_RETRY_MODE", mode="before")
    @classmethod
    def normalize_string_fields(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("S3_SIGNATURE_VERSION")
    @classmethod
    def default_signature_version(cls, value: str) -> str:
        return value or "s3v4"

    @field_validator("S3_RETRY_MODE")
    @classmethod
    def default_retry_mode(cls, value: str) -> str:
        return value or "standard"

    @model_validator(mode="after")
    def require_credentials_when_enabled(self) -> "S3Config":
        if not self.S3_ENABLED:
            return self
        missing = [
            name
            for name in (
                "S3_BUCKET_NAME",
                "S3_ACCESS_KEY_ID",
                "S3_SECRET_ACCESS_KEY",
                "S3_REGION_NAME",
            )
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"S3_ENABLED=true requires: {', '.join(missing)}")
        return self


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
    REDIS_TASKS_DATABASE: str = "1"

    model_config = ConfigDict(extra="ignore")

    @property
    def dsn(self) -> str:
        # quote(safe="") because generated passwords routinely contain @ / % :
        # and every URL parser downstream would silently misread the DSN.
        return (
            f"redis://:"
            f"{quote(self.REDIS_PASSWORD, safe='')}@"
            f"{self.REDIS_HOST}:"
            f"{self.REDIS_PORT}/"
            f"{self.REDIS_DATABASE}"
        )

    @property
    def tasks_dsn(self) -> str:
        return (
            f"redis://:"
            f"{quote(self.REDIS_PASSWORD, safe='')}@"
            f"{self.REDIS_HOST}:"
            f"{self.REDIS_PORT}/"
            f"{self.REDIS_TASKS_DATABASE}"
        )


class CacheConfig(BaseModel):
    CACHE_ENABLED: bool = True
    CACHE_DEFAULT_TTL: int = Field(60, gt=0)
    CACHE_VERSION_TTL: int = Field(604800, gt=0)
    CACHE_KEY_PREFIX: str = "cache"

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def validate_ttl_bounds(self) -> "CacheConfig":
        # Values must die before their namespace version counter, otherwise an
        # expired counter resets the version to 0 and resurrects stale values.
        if self.CACHE_DEFAULT_TTL > self.CACHE_VERSION_TTL:
            raise ValueError("CACHE_DEFAULT_TTL must not exceed CACHE_VERSION_TTL")
        return self


class SentryConfig(BaseModel):
    SENTRY_DSN: str | None = None
    SENTRY_ENV: str = "development"
    SENTRY_ENABLED: bool = False

    model_config = ConfigDict(extra="ignore")


class CookieConfig(BaseModel):
    """Policy for auth cookies. Applied by TokenCookieResponder."""

    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_DOMAIN: str | None = None
    CSRF_SECRET_KEY: str = Field(min_length=SECRET_MIN_LENGTH)

    model_config = ConfigDict(extra="ignore")

    @field_validator("COOKIE_DOMAIN", mode="before")
    @classmethod
    def normalize_optional_domain(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        return str(value)

    @model_validator(mode="after")
    def reject_samesite_none_without_secure(self) -> "CookieConfig":
        """
        Fail startup on a combination browsers silently discard.

        A `SameSite=None` cookie without `Secure` is rejected by every modern
        browser, so the app would boot cleanly and then drop every auth cookie at
        the client with no server-side signal at all.
        """
        if self.COOKIE_SAMESITE == "none" and not self.COOKIE_SECURE:
            raise ValueError(
                "COOKIE_SAMESITE=none requires COOKIE_SECURE=true: browsers reject "
                "a SameSite=None cookie that is not Secure."
            )
        return self


class JWTConfig(BaseModel):
    # A signing key shorter than the HMAC block size weakens HS256 and is almost
    # always a placeholder left over from .env.example.
    JWT_USER_SECRET_KEY: str = Field(min_length=SECRET_MIN_LENGTH)
    JWT_VERIFY_SECRET_KEY: str = Field(min_length=SECRET_MIN_LENGTH)
    JWT_RESET_PASSWORD_SECRET_KEY: str = Field(min_length=SECRET_MIN_LENGTH)

    ALGORITHM: Literal["HS256", "HS384", "HS512"]

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

    DB_POOL_SIZE: int = Field(5, gt=0)
    DB_MAX_OVERFLOW: int = Field(2, ge=0)
    # Tasks pool size + overflow must cover --max-async-tasks on the worker
    # command (infra/docker-compose.yml): every concurrent task may hold a
    # session, and an undersized pool surfaces as pool_timeout errors.
    DB_TASKS_POOL_SIZE: int = Field(5, gt=0)
    DB_TASKS_MAX_OVERFLOW: int = Field(15, ge=0)

    model_config = ConfigDict(extra="ignore")

    @property
    def dsn_async(self) -> str:
        # quote(safe="") because generated credentials routinely contain @ / % :
        # and every URL parser downstream would silently misread the DSN.
        return (
            f"postgresql+asyncpg://"
            f"{quote(self.POSTGRES_USER, safe='')}:"
            f"{quote(self.POSTGRES_PASSWORD, safe='')}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )

    @property
    def dsn_sync(self) -> str:
        return (
            f"postgresql://"
            f"{quote(self.POSTGRES_USER, safe='')}:"
            f"{quote(self.POSTGRES_PASSWORD, safe='')}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )


class AppConfig(BaseModel):
    VERSION: str
    DEBUG: bool = False
    TESTING: bool = False

    # Read by loggers/ straight from os.environ, never through this field; it
    # stays here so startup and check_env keep requiring the key in .env.
    LOG_LEVEL: str

    CORS_ALLOWED_ORIGINS: list[str] = Field([])
    CORS_ALLOWED_CREDENTIALS: bool = True
    CORS_ALLOWED_METHODS: list[str] = Field(["*"])
    CORS_ALLOWED_HEADERS: list[str] = Field(["*"])
    CORS_EXPOSE_HEADERS: list[str] = Field(["*"])

    TRUST_PROXY_HEADERS: bool
    TRUST_PROXY_HOSTS: list[str] = Field(
        [
            "127.0.0.1",
            "::1",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "fc00::/7",
        ]
    )

    PROJECT_NAME: str

    # Origin of the front-end that receives users arriving from an email. Links
    # are built from it and never from the request, because Host is client input
    # and a reset link pointed at another domain hands over the account.
    PUBLIC_BASE_URL: str
    EMAIL_VERIFY_PATH: str = "/verify-email"
    PASSWORD_RESET_PATH: str = "/reset-password"

    # Interactive docs stay open in DEBUG. Outside it they are served only when
    # both credentials are set; unset means the app publishes no docs at all.
    DOCS_USERNAME: str = ""
    DOCS_PASSWORD: str = ""

    model_config = ConfigDict(extra="ignore")

    @field_validator(
        "CORS_ALLOWED_ORIGINS",
        "CORS_ALLOWED_METHODS",
        "CORS_ALLOWED_HEADERS",
        "CORS_EXPOSE_HEADERS",
        "TRUST_PROXY_HOSTS",
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

    @field_validator("PUBLIC_BASE_URL")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        url = value.strip().rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                "PUBLIC_BASE_URL must be an absolute http(s) URL, "
                "for example https://app.example.com"
            )
        if any(char.isspace() for char in url):
            raise ValueError("PUBLIC_BASE_URL must not contain whitespace")
        if parsed.query or parsed.fragment:
            raise ValueError(
                "PUBLIC_BASE_URL must be an origin with an optional path, "
                "without a query string or a fragment: the token is appended "
                "to it as a query parameter"
            )
        return url

    @field_validator("EMAIL_VERIFY_PATH", "PASSWORD_RESET_PATH")
    @classmethod
    def normalize_public_path(cls, value: str) -> str:
        return "/" + value.strip().lstrip("/")

    @model_validator(mode="after")
    def reject_wildcard_origin_with_credentials(self) -> "AppConfig":
        """
        Fail startup on the combination that opens the API to every site.

        Starlette answers a wildcard allowlist by echoing the request's own
        Origin back, so paired with allow_credentials any third-party page can
        make credentialed cross-origin calls and read the responses. "null" is
        the same hole wearing a different hat: a sandboxed iframe or a data:
        document sends `Origin: null`, and any page can open one.
        """
        unsafe = {"*", "null"} & {
            origin.strip().lower() for origin in self.CORS_ALLOWED_ORIGINS
        }
        if unsafe and self.CORS_ALLOWED_CREDENTIALS:
            raise ValueError(
                f"CORS_ALLOWED_ORIGINS={sorted(unsafe)[0]} cannot be combined "
                "with CORS_ALLOWED_CREDENTIALS=true: list the front-end origins "
                "explicitly, or turn credentials off."
            )
        return self

    @model_validator(mode="after")
    def reject_wildcard_proxy_trust(self) -> "AppConfig":
        """
        Fail startup when every proxy hop is trusted.

        With a wildcard nothing in the forwarded chain is attacker-free, so the
        resolver has no honest end to start from and the rate limiter ends up
        keyed by a value the caller writes. List the edge addresses or ranges
        instead - for a CDN, the published ranges of that CDN.
        """
        if "*" in [host.strip() for host in self.TRUST_PROXY_HOSTS]:
            raise ValueError(
                "TRUST_PROXY_HOSTS=* would trust the whole X-Forwarded-For "
                "chain, which lets any caller pick their own rate-limit "
                "identity: list the proxy addresses or CIDR ranges instead."
            )
        return self

    @model_validator(mode="after")
    def validate_docs_credentials(self) -> "AppConfig":
        """
        Keep the docs password brute-force resistant, or absent.

        Both blank means the docs are simply not published, which is a valid
        choice. Once they are published, the password is the only thing in
        front of a full map of the API, so it answers to the same minimum as
        every other secret. HTTP Basic transports credentials as base64 of a
        latin-1 string and FastAPI decodes them as ASCII, so a non-ASCII
        password would lock the operator out with no way to tell why.
        """
        if not (self.DOCS_USERNAME or self.DOCS_PASSWORD):
            return self

        if not (self.DOCS_USERNAME and self.DOCS_PASSWORD):
            raise ValueError(
                "DOCS_USERNAME and DOCS_PASSWORD must both be set to publish "
                "the docs, or both be empty to keep them unpublished."
            )
        if len(self.DOCS_PASSWORD) < SECRET_MIN_LENGTH:
            raise ValueError(
                f"DOCS_PASSWORD must be at least {SECRET_MIN_LENGTH} characters"
            )
        if not (self.DOCS_USERNAME.isascii() and self.DOCS_PASSWORD.isascii()):
            raise ValueError("DOCS_USERNAME and DOCS_PASSWORD must be ASCII-only")
        return self


class Config(BaseModel):
    app: AppConfig
    s3: S3Config
    jwt: JWTConfig
    redis: RedisConfig
    cache: CacheConfig
    sentry: SentryConfig
    cookie: CookieConfig
    postgres: PostgresConfig
    broadcasting: BroadcastingConfig

    model_config = ConfigDict(extra="ignore")


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
        s3=S3Config(**merged_env),
        jwt=JWTConfig(**merged_env),
        redis=RedisConfig(**merged_env),
        cache=CacheConfig(**merged_env),
        sentry=SentryConfig(**merged_env),
        cookie=CookieConfig(**merged_env),
        postgres=PostgresConfig(**merged_env),
        broadcasting=BroadcastingConfig(**merged_env),
    )


config = get_settings()
