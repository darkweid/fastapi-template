from src.core.email.config import get_fastapi_mail_config
from src.core.email.fastapi_mailer import FastAPIMailer
from src.core.email.service import EmailService


def get_email_service() -> EmailService:
    config = get_fastapi_mail_config()
    mailer = FastAPIMailer(config)
    return EmailService(mailer)
