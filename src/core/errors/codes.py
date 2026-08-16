from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable machine-readable error codes: the `code` field of every error body.

    This enum is the complete client-facing registry, including the two codes
    produced only by the nginx error pages (`infra/nginx/proxy.inc`). A frontend
    keeps a `code -> translation` map; `message` is the English fallback.
    """

    INTERNAL_ERROR = "internal_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    TOKEN_EXPIRED = "token_expired"  # nosec B105
    FORBIDDEN = "forbidden"
    PERMISSION_DENIED = "permission_denied"
    CSRF_FAILED = "csrf_failed"
    USER_BLOCKED = "user_blocked"
    USER_NOT_VERIFIED = "user_not_verified"
    INVALID_CREDENTIALS = "invalid_credentials"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    INVALID_REFERENCE = "invalid_reference"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    RATE_LIMITED = "rate_limited"
    INVALID_QUERY = "invalid_query"
    PROCESSING_ERROR = "processing_error"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    # Produced only by the nginx error pages, never by the application.
    BAD_GATEWAY = "bad_gateway"
    GATEWAY_TIMEOUT = "gateway_timeout"
