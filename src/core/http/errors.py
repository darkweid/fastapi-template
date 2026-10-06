from src.core.errors.exceptions import InfrastructureException


class HttpTransportError(InfrastructureException):
    """No status came back, or the answer was refused.

    Carries the caller's operation name and the failure class only: the URL
    may hold a token, and aiohttp's own messages quote the URL and sometimes
    request headers. `request_sent` is False only when the server cannot have
    seen the request; `transient` is False when retrying cannot help. The
    defaults claim the least: possibly sent, not worth repeating.
    """

    def __init__(
        self,
        operation: str,
        reason: str = "unknown",
        *,
        request_sent: bool = True,
        transient: bool = False,
    ) -> None:
        super().__init__(f"{operation}: {reason}")
        self.operation = operation
        self.reason = reason
        self.request_sent = request_sent
        self.transient = transient
