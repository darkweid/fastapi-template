from enum import StrEnum


class MessageType(StrEnum):
    """Content type of an outgoing email body."""

    HTML = "html"
    PLAIN = "plain"
