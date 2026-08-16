from typing import Annotated, cast

from fastapi import Depends, Request

from src.core.errors.exceptions import InfrastructureException
from src.core.storage.s3.adapter import S3Adapter
from src.core.storage.s3.interface import S3ClientProtocol
from src.main.config import Config, S3Config, get_settings


def build_s3_adapter(s3_config: S3Config) -> S3Adapter:
    """Construct the S3 adapter from config. The caller owns entering/exiting it."""
    # The require_credentials_when_enabled validator guarantees these are set
    # whenever S3_ENABLED is true; this re-check narrows the Optional types
    # without assert, which disappears under `python -O`.
    if (
        s3_config.S3_BUCKET_NAME is None
        or s3_config.S3_REGION_NAME is None
        or s3_config.S3_ACCESS_KEY_ID is None
        or s3_config.S3_SECRET_ACCESS_KEY is None
    ):
        raise InfrastructureException("S3 credentials are not configured")
    return S3Adapter(
        bucket=s3_config.S3_BUCKET_NAME,
        region=s3_config.S3_REGION_NAME,
        access_key=s3_config.S3_ACCESS_KEY_ID,
        secret_key=s3_config.S3_SECRET_ACCESS_KEY,
        default_presign_ttl=s3_config.S3_PRE_SIGNED_URL_SECONDS,
        endpoint_url=s3_config.S3_ENDPOINT_URL,
        addressing_style=s3_config.S3_ADDRESSING_STYLE,
        signature_version=s3_config.S3_SIGNATURE_VERSION,
        verify_ssl=s3_config.S3_VERIFY_SSL,
        ca_bundle=s3_config.S3_CA_BUNDLE,
        treat_access_denied_as_missing=s3_config.S3_TREAT_ACCESS_DENIED_AS_MISSING,
        connect_timeout_seconds=s3_config.S3_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds=s3_config.S3_READ_TIMEOUT_SECONDS,
        retry_max_attempts=s3_config.S3_RETRY_MAX_ATTEMPTS,
        retry_mode=s3_config.S3_RETRY_MODE,
        max_upload_size_bytes=s3_config.S3_MAX_UPLOAD_SIZE_BYTES,
    )


async def get_s3_adapter(
    request: Request,
    settings: Annotated[Config, Depends(get_settings)],
) -> S3ClientProtocol:
    if not settings.s3.S3_ENABLED:
        raise InfrastructureException(
            "S3 is disabled: set S3_ENABLED=true and provide S3 credentials"
        )
    # app.state is typed as an opaque namespace, so mypy sees Any here; the
    # attribute is only ever set by lifespan's build_s3_adapter() call.
    return cast(S3ClientProtocol, request.app.state.s3_adapter)
