from src.core.schemas import Base


class MailTemplateDataBody(Base):
    title: str
    link: str


class MailTemplateBodyFile(Base):
    title: str
    file: str


class MailTemplateVerificationBody(Base):
    title: str
    link: str
    name: str


class MailTemplateNotificationBody(MailTemplateVerificationBody):
    pass


class MailTemplateResetPasswordBody(MailTemplateVerificationBody):
    pass
