from pydantic import ValidationError
import pytest
from redis.connection import parse_url
from sqlalchemy.engine import make_url

from src.main.config import (
    AppConfig,
    CacheConfig,
    JWTConfig,
    PostgresConfig,
    RedisConfig,
    S3Config,
)


def _base_jwt_config_data() -> dict[str, object]:
    return {
        "JWT_USER_SECRET_KEY": "unit-test-user-secret-key-long-enough",
        "JWT_VERIFY_SECRET_KEY": "unit-test-verify-secret-key-long-enough",
        "JWT_RESET_PASSWORD_SECRET_KEY": "unit-test-reset-secret-key-long-enough",
        "ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": 15,
        "REFRESH_TOKEN_EXPIRE_MINUTES": 129_600,
        "VERIFICATION_TOKEN_EXPIRE_MINUTES": 180,
        "RESET_PASSWORD_TOKEN_EXPIRE_MINUTES": 180,
    }


def _base_app_config_data() -> dict[str, object]:
    return {
        "VERSION": "1.0.0",
        "DEBUG": False,
        "LOG_LEVEL": "INFO",
        "CORS_ALLOWED_ORIGINS": "https://app.example.com",
        "CORS_ALLOWED_CREDENTIALS": True,
        "CORS_ALLOWED_METHODS": "*",
        "CORS_ALLOWED_HEADERS": "*",
        "CORS_EXPOSE_HEADERS": "*",
        "TRUST_PROXY_HEADERS": "true",
        "PROJECT_NAME": "app",
        "PUBLIC_BASE_URL": "https://app.example.com",
    }


def test_parse_cors_list_json_string() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = '["https://a.com", "https://b.com"]'

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_ORIGINS == ["https://a.com", "https://b.com"]


def test_parse_cors_list_semicolon_delimiter() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_METHODS"] = "GET;POST;PUT"

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_METHODS == ["GET", "POST", "PUT"]


def test_parse_trust_proxy_hosts_json_string() -> None:
    data = _base_app_config_data()
    data["TRUST_PROXY_HOSTS"] = '["127.0.0.1", "::1", "10.0.0.0/8"]'

    app_config = AppConfig(**data)

    assert app_config.TRUST_PROXY_HOSTS == ["127.0.0.1", "::1", "10.0.0.0/8"]


def test_app_config_reads_cors_allowed_credentials_flag() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_CREDENTIALS"] = False

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_CREDENTIALS is False


def test_app_config_defaults_to_no_cors_origins() -> None:
    data = _base_app_config_data()
    del data["CORS_ALLOWED_ORIGINS"]

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_ORIGINS == []


def test_app_config_rejects_wildcard_origin_with_credentials() -> None:
    # Starlette echoes back any Origin when this pair is set, which turns every
    # third-party page into an authenticated client of this API.
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = "*"
    data["CORS_ALLOWED_CREDENTIALS"] = True

    with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS=\\*"):
        AppConfig(**data)


def test_app_config_rejects_wildcard_among_explicit_origins_with_credentials() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = "https://app.example.com,*"
    data["CORS_ALLOWED_CREDENTIALS"] = True

    with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS=\\*"):
        AppConfig(**data)


def test_app_config_allows_wildcard_origin_without_credentials() -> None:
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = "*"
    data["CORS_ALLOWED_CREDENTIALS"] = False

    app_config = AppConfig(**data)

    assert app_config.CORS_ALLOWED_ORIGINS == ["*"]


def test_cache_config_defaults() -> None:
    cache_config = CacheConfig()

    assert cache_config.CACHE_ENABLED is True
    assert cache_config.CACHE_DEFAULT_TTL == 60
    assert cache_config.CACHE_VERSION_TTL == 604800
    assert cache_config.CACHE_KEY_PREFIX == "cache"


def test_cache_config_rejects_default_ttl_above_version_ttl() -> None:
    with pytest.raises(ValueError, match="CACHE_VERSION_TTL"):
        CacheConfig(CACHE_DEFAULT_TTL=100, CACHE_VERSION_TTL=50)


def test_jwt_config_rejects_short_secret() -> None:
    with pytest.raises(ValidationError):
        JWTConfig(**{**_base_jwt_config_data(), "JWT_USER_SECRET_KEY": "too-short"})


def test_jwt_config_rejects_unknown_algorithm() -> None:
    with pytest.raises(ValidationError):
        JWTConfig(**{**_base_jwt_config_data(), "ALGORITHM": "none"})


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_jwt_config_accepts_allowlisted_algorithms(algorithm: str) -> None:
    jwt_config = JWTConfig(**{**_base_jwt_config_data(), "ALGORITHM": algorithm})

    assert jwt_config.ALGORITHM == algorithm


def test_app_config_ships_no_docs_credentials_by_default() -> None:
    app_config = AppConfig(**_base_app_config_data())

    assert app_config.DOCS_USERNAME == ""
    assert app_config.DOCS_PASSWORD == ""


def test_app_config_requires_public_base_url() -> None:
    data = _base_app_config_data()
    del data["PUBLIC_BASE_URL"]

    with pytest.raises(ValidationError):
        AppConfig(**data)


def test_app_config_rejects_public_base_url_without_scheme() -> None:
    data = _base_app_config_data()
    data["PUBLIC_BASE_URL"] = "app.example.com"

    with pytest.raises(ValueError, match="absolute http\\(s\\) URL"):
        AppConfig(**data)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://", id="scheme-only"),
        pytest.param("https:///path", id="empty-host"),
        pytest.param("//app.example.com", id="scheme-relative"),
        pytest.param("javascript:alert(1)", id="javascript"),
        pytest.param("http:// app.example.com", id="whitespace"),
    ],
)
def test_app_config_rejects_unusable_public_base_url(url: str) -> None:
    data = _base_app_config_data()
    data["PUBLIC_BASE_URL"] = url

    with pytest.raises(ValidationError):
        AppConfig(**data)


def test_app_config_rejects_public_base_url_carrying_a_query() -> None:
    """The token is appended as a query parameter, so a base with one is broken."""
    data = _base_app_config_data()
    data["PUBLIC_BASE_URL"] = "https://app.example.com?utm=email"

    with pytest.raises(ValueError, match="without a query string"):
        AppConfig(**data)


def test_app_config_rejects_null_origin_with_credentials() -> None:
    """
    `Origin: null` is what a sandboxed iframe or a data: document sends, and any
    page can open one - allowing it with credentials is the wildcard hole again.
    """
    data = _base_app_config_data()
    data["CORS_ALLOWED_ORIGINS"] = "null"

    with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS"):
        AppConfig(**data)


def test_app_config_rejects_wildcard_proxy_trust() -> None:
    data = _base_app_config_data()
    data["TRUST_PROXY_HOSTS"] = "*"

    with pytest.raises(ValueError, match="TRUST_PROXY_HOSTS"):
        AppConfig(**data)


def test_app_config_accepts_explicit_proxy_ranges() -> None:
    data = _base_app_config_data()
    data["TRUST_PROXY_HOSTS"] = "10.0.0.0/8,172.16.0.0/12"

    app_config = AppConfig(**data)

    assert app_config.TRUST_PROXY_HOSTS == ["10.0.0.0/8", "172.16.0.0/12"]


def test_app_config_rejects_a_short_docs_password() -> None:
    data = _base_app_config_data()
    data["DOCS_USERNAME"] = "docs"
    data["DOCS_PASSWORD"] = "admin"

    with pytest.raises(ValueError, match="DOCS_PASSWORD"):
        AppConfig(**data)


def test_app_config_rejects_a_non_ascii_docs_password() -> None:
    """
    HTTP Basic reaches FastAPI as ASCII, so a non-ASCII password would lock the
    operator out with a plain 401 and no way to tell why.
    """
    data = _base_app_config_data()
    data["DOCS_USERNAME"] = "docs"
    data["DOCS_PASSWORD"] = "пароль-длиннее-тридцати-двух-символов"

    with pytest.raises(ValueError, match="ASCII-only"):
        AppConfig(**data)


def test_app_config_rejects_half_configured_docs_credentials() -> None:
    data = _base_app_config_data()
    data["DOCS_PASSWORD"] = "docs-password-long-enough-to-pass"

    with pytest.raises(ValueError, match="both be empty"):
        AppConfig(**data)


def test_app_config_accepts_full_docs_credentials() -> None:
    data = _base_app_config_data()
    data["DOCS_USERNAME"] = "docs"
    data["DOCS_PASSWORD"] = "docs-password-long-enough-to-pass"

    app_config = AppConfig(**data)

    assert app_config.DOCS_USERNAME == "docs"


def test_app_config_normalizes_public_base_url_and_paths() -> None:
    data = _base_app_config_data()
    data["PUBLIC_BASE_URL"] = "https://app.example.com/"
    data["EMAIL_VERIFY_PATH"] = "confirm-email"

    app_config = AppConfig(**data)

    assert app_config.PUBLIC_BASE_URL == "https://app.example.com"
    assert app_config.EMAIL_VERIFY_PATH == "/confirm-email"
    assert app_config.PASSWORD_RESET_PATH == "/reset-password"


def test_s3_disabled_needs_no_credentials() -> None:
    assert S3Config().S3_ENABLED is False  # no env → still constructs


def test_s3_enabled_requires_credentials() -> None:
    with pytest.raises(ValidationError, match="S3_ENABLED=true requires"):
        S3Config(S3_ENABLED=True, S3_BUCKET_NAME="b")  # missing the rest


def test_redis_dsn_escapes_special_characters_in_password() -> None:
    raw_password = "p@ss/word%:x"
    redis_config = RedisConfig(
        REDIS_HOST="redis-host",
        REDIS_PORT=6379,
        REDIS_PASSWORD=raw_password,
        REDIS_DATABASE="0",
    )

    parsed = parse_url(redis_config.dsn)

    assert parsed["password"] == raw_password
    assert parsed["host"] == "redis-host"
    assert parsed["db"] == 0


def test_postgres_dsn_escapes_special_characters_in_credentials() -> None:
    raw_password = "p@ss/word%:x"
    postgres_config = PostgresConfig(
        DB_ECHO=False,
        POSTGRES_USER="app:user",
        POSTGRES_PASSWORD=raw_password,
        POSTGRES_HOST="db-host",
        POSTGRES_PORT=5432,
        POSTGRES_DB="app",
    )

    for dsn in (postgres_config.dsn_async, postgres_config.dsn_sync):
        url = make_url(dsn)
        assert url.username == "app:user"
        assert url.password == raw_password
        assert url.host == "db-host"
        assert url.database == "app"


def test_postgres_pool_sizes_default_and_validate() -> None:
    postgres_config = PostgresConfig(
        DB_ECHO=False,
        POSTGRES_USER="app",
        POSTGRES_PASSWORD="secret",
        POSTGRES_HOST="db-host",
        POSTGRES_PORT=5432,
        POSTGRES_DB="app",
    )

    assert postgres_config.DB_POOL_SIZE == 5
    assert postgres_config.DB_MAX_OVERFLOW == 2
    assert postgres_config.DB_TASKS_POOL_SIZE == 5
    assert postgres_config.DB_TASKS_MAX_OVERFLOW == 15

    with pytest.raises(ValidationError):
        PostgresConfig(
            DB_ECHO=False,
            POSTGRES_USER="app",
            POSTGRES_PASSWORD="secret",
            POSTGRES_HOST="db-host",
            POSTGRES_PORT=5432,
            POSTGRES_DB="app",
            DB_POOL_SIZE=0,
        )
