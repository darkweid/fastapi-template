from collections.abc import AsyncGenerator

from fastapi import Depends

from src.core.storage.s3.adapter import S3Adapter
from src.core.storage.s3.interface import S3ClientProtocol
from src.main.config import Config, get_settings


async def get_s3_adapter(
    settings: Config = Depends(get_settings),
) -> AsyncGenerator[S3ClientProtocol]:
    s3 = settings.s3
    async with S3Adapter(
        bucket=s3.S3_BUCKET_NAME,
        region=s3.S3_REGION_NAME,
        access_key=s3.S3_ACCESS_KEY_ID,
        secret_key=s3.S3_SECRET_ACCESS_KEY,
        default_presign_ttl=s3.S3_PRE_SIGNED_URL_SECONDS,
        endpoint_url=s3.S3_ENDPOINT_URL,
        addressing_style=s3.S3_ADDRESSING_STYLE,
        signature_version=s3.S3_SIGNATURE_VERSION,
        verify_ssl=s3.S3_VERIFY_SSL,
        ca_bundle=s3.S3_CA_BUNDLE,
        treat_access_denied_as_missing=s3.S3_TREAT_ACCESS_DENIED_AS_MISSING,
        connect_timeout_seconds=s3.S3_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds=s3.S3_READ_TIMEOUT_SECONDS,
        retry_max_attempts=s3.S3_RETRY_MAX_ATTEMPTS,
        retry_mode=s3.S3_RETRY_MODE,
        max_upload_size_bytes=s3.S3_MAX_UPLOAD_SIZE_BYTES,
    ) as adapter:
        yield adapter
