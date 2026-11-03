"""Exception hierarchy used by the service layer.

The UI catches ``ServiceError`` and shows ``str(error)`` to the user, so every
message must be safe to display (no SQL, no stack traces, no secrets).
"""


class ServiceError(Exception):
    """Base class for all expected, user-presentable errors."""


class ValidationError(ServiceError):
    """Input failed a business rule."""


class NotFoundError(ServiceError):
    """The requested record does not exist."""


class AuthenticationError(ServiceError):
    """Login failed or no valid session."""


class AuthorizationError(ServiceError):
    """The record exists but belongs to another user."""


class ConflictError(ServiceError):
    """The change would create a duplicate or violate a uniqueness rule."""
