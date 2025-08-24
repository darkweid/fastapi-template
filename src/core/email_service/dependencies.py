from src.core.email_service.config import get_fastapi_mail_config
from src.core.email_service.fastapi_mailer import FastAPIMailer
from src.core.email_service.service import EmailService


def get_email_service() -> EmailService:
    config = get_fastapi_mail_config()
    mailer = FastAPIMailer(config)
    return EmailService(mailer)
