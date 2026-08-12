from src.core.email_service.service import EmailService
from src.core.email_service.tasks import get_mailer


def get_email_service() -> EmailService:
    return EmailService(get_mailer())
