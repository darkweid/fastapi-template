from fastapi_mail import ConnectionConfig
from pathlib import Path
from app.core.settings import settings

email_config = ConnectionConfig(
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
    VALIDATE_CERTS=False,
)
