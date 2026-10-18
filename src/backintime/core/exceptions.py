"""Typed application exceptions for actionable user-facing errors."""


class BackInTimeError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(BackInTimeError):
    """Raised when configuration cannot be loaded, validated, or saved."""


class ConfigurationConflictError(BackInTimeError):
    """Raised when configuration changed since a caller last read it."""


class ValidationError(BackInTimeError):
    """Raised when user-provided values or paths are invalid."""


class RepositoryError(BackInTimeError):
    """Raised when a snapshot repository cannot be safely inspected."""


class BackupCancelledError(BackInTimeError):
    """Raised when a backup reaches a safe cancellation point."""


class JobStoreError(BackInTimeError):
    """Raised when background job state cannot be loaded or persisted."""


class JobConflictError(BackInTimeError):
    """Raised when a requested job conflicts with one that is queued or running."""
