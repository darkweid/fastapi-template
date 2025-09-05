from typing import Any


class CoreException(Exception):
    def __init__(
        self, message: str | None = None, additional_info: dict[str, Any] | None = None
    ):
        self.message = message
        self.additional_info = additional_info


class InstanceNotFoundException(CoreException): ...


class InstanceAlreadyExistsException(CoreException): ...


class InstanceProcessingException(CoreException): ...


class FilteringError(CoreException):
    pass


class UnauthorizedException(CoreException):
    pass


class AccessForbiddenException(CoreException):
    pass


class NotAcceptableException(CoreException):
    pass


class PermissionDeniedException(CoreException):
    pass
