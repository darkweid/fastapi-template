from src.core.schemas import Base


class MailTemplateDataBody(Base):
    title: str
    link: str


class MailTemplateBodyFile(Base):
    title: str
    file: str
