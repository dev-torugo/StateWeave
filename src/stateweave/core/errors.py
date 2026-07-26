"""Domain exceptions for the memory core."""

from __future__ import annotations


class StateWeaveError(Exception):
    """Base class for expected StateWeave failures."""


class ConfigurationError(StateWeaveError):
    """The versioned project configuration is invalid."""


class PathBoundaryError(StateWeaveError):
    """A configured or supplied path escapes the project root."""


class RecordError(StateWeaveError):
    """A record cannot be loaded or written safely."""


class LockUnavailableError(StateWeaveError):
    """The exclusive writer lock could not be acquired."""

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


class BackupError(StateWeaveError):
    """A backup or restore operation failed validation."""


class MigrationError(StateWeaveError):
    """A migration could not complete or roll back safely."""


class ContractError(StateWeaveError):
    """An optional-module document violates its public contract."""
