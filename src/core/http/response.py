import codecs
from dataclasses import dataclass
from email.message import Message
import json as jsonlib

from multidict import CIMultiDictProxy


def _charset(headers: CIMultiDictProxy[str]) -> str:
    message = Message()
    message["Content-Type"] = headers.get("Content-Type", "")
    charset = message.get_content_charset()
    if charset is None:
        return "utf-8"
    try:
        return codecs.lookup(charset).name
    except LookupError:
        return "utf-8"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """An answer already read off the wire, so no caller holds a connection."""

    status: int
    headers: CIMultiDictProxy[str]
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self) -> str:
        """Decoded with the charset of Content-Type, UTF-8 when it names none
        or one Python does not know; undecodable bytes are replaced."""
        return self.body.decode(_charset(self.headers), errors="replace")

    def json(self) -> object:
        """Raises ValueError on a body that is not JSON, RecursionError on one
        nested past the parser's depth."""
        return jsonlib.loads(self.body)

    def json_or_none(self) -> object:
        """None for an unreadable body: the status, not the body, decides the
        failure class."""
        try:
            return self.json()
        # A deeply nested body exhausts the parser's recursion instead.
        except (ValueError, RecursionError):
            return None
