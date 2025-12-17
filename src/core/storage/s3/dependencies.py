from collections.abc import AsyncGenerator

from fastapi import Depends

from src.core.storage.s3.adapter import S3Adapter
from src.core.storage.s3.interface import S3ClientProtocol
from src.main.config import Config, get_settings


async def get_s3_adapter(
    settings: Config = Depends(get_settings),
) -> AsyncGenerator[S3ClientProtocol]:
    aws = settings.aws
    async with S3Adapter(
        bucket=aws.BUCKET_NAME,
        region=aws.REGION_NAME,
        access_key=aws.AWS_ACCESS_KEY_ID,
        secret_key=aws.AWS_SECRET_ACCESS_KEY,
        default_presign_ttl=aws.PRE_SIGNED_URL_SECONDS,
    ) as adapter:
        yield adapter
