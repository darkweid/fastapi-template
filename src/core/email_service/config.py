from pathlib import Path

from fastapi_mail import ConnectionConfig

from src.main.config import config

email_config = config.broadcasting


def get_fastapi_mail_config() -> ConnectionConfig:
    return ConnectionConfig(
        MAIL_USERNAME=email_config.EMAIL_USER,
        MAIL_PASSWORD=email_config.EMAIL_PASSWORD,
        MAIL_FROM=email_config.EMAIL_USER,
        MAIL_PORT=email_config.EMAIL_PORT,
        MAIL_SERVER=email_config.EMAIL_SERVER,
        MAIL_FROM_NAME=email_config.EMAIL_FROM_NAME,
        MAIL_STARTTLS=email_config.EMAIL_STARTTLS,
        MAIL_SSL_TLS=email_config.EMAIL_USE_TLS,
        USE_CREDENTIALS=True,
        TEMPLATE_FOLDER=Path(__file__).parent / "templates",
        VALIDATE_CERTS=email_config.VALIDATE_CERTS,
    )
