from app.email.config import get_fastapi_mail_config
from app.email.fastapi_mailer import FastAPIMailer
from app.email.service import EmailService


def get_email_service() -> EmailService:
    config = get_fastapi_mail_config()
    mailer = FastAPIMailer(config)
    return EmailService(mailer)
