from typing import Any


class CoreException(Exception):
    def __init__(
        self, message: str | None = None, additional_info: dict[str, Any] | None = None
    ):
        self.message = message
        self.additional_info = additional_info


class InfrastructureException(CoreException):
    pass


class InstanceNotFoundException(CoreException):
    pass


class InstanceAlreadyExistsException(CoreException):
    pass


class InstanceProcessingException(CoreException):
    pass


class PayloadTooLargeException(CoreException):
    pass


class FilteringError(CoreException):
    pass


class UnauthorizedException(CoreException):
    def __init__(
        self,
        message: str | None = None,
        www_authenticate: str | None = None,
        additional_info: dict[str, Any] | None = None,
    ):
        super().__init__(message, additional_info)
        # Set it only for schemes the client is expected to answer, such as the
        # Basic challenge that makes a browser show a login prompt for the docs.
        self.www_authenticate = www_authenticate


class AccessForbiddenException(CoreException):
    pass


class NotAcceptableException(CoreException):
    pass


class PermissionDeniedException(CoreException):
    pass


class TooManyRequestsException(CoreException):
    def __init__(
        self,
        message: str | None = None,
        retry_after: int | None = None,
        additional_info: dict[str, Any] | None = None,
    ):
        super().__init__(message, additional_info)
        self.retry_after = retry_after
