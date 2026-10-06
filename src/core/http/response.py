from dataclasses import dataclass
import json as jsonlib

from multidict import CIMultiDictProxy


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
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        """Raises ValueError on a body that is not JSON."""
        return jsonlib.loads(self.body)

    def json_or_none(self) -> object:
        """None for an unreadable body: the status, not the body, decides the
        failure class."""
        try:
            return self.json()
        # A deeply nested body exhausts the parser's recursion instead.
        except (ValueError, RecursionError):
            return None
