from pathlib import Path
from fastapi_mail import ConnectionConfig
from src.core.settings import settings


def get_fastapi_mail_config() -> ConnectionConfig:
    return ConnectionConfig(
        MAIL_USERNAME=settings.email_user,
        MAIL_PASSWORD=settings.email_password,
        MAIL_FROM=settings.email_user,
        MAIL_PORT=settings.email_port,
        MAIL_SERVER=settings.email_server,
        MAIL_FROM_NAME=settings.mail_from_name,
        MAIL_STARTTLS=True,
        MAIL_SSL_TLS=False,
        USE_CREDENTIALS=True,
        TEMPLATE_FOLDER=Path(__file__).parent / "templates",
        VALIDATE_CERTS=not settings.debug,  # enable cert validation in production
    )
