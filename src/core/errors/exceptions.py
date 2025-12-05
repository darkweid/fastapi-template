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
