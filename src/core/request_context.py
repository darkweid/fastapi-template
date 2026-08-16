from contextvars import ContextVar
import re
import uuid

# Set by the request-id middleware for the lifetime of one request, read by
# the logging filter so every log line emitted while handling it carries the
# same id. None outside a request (worker tasks, startup code).
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Inbound X-Request-ID is client-controlled and lands in logs verbatim, so it
# is constrained to a conservative charset/length before being echoed back -
# anything else (empty, too long, containing CR/LF or other injection-prone
# characters) is replaced with a freshly generated id instead.
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def get_request_id() -> str | None:
    """Return the id of the request currently being handled, if any."""
    return request_id_var.get()


def resolve_request_id(inbound: str | None) -> str:
    """Echo a safe inbound id, generate uuid4().hex otherwise."""
    if inbound is not None and _REQUEST_ID_PATTERN.match(inbound):
        return inbound
    return uuid.uuid4().hex
