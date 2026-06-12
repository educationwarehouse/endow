"""Framework-agnostic policy primitives for authorization-aware domains."""

from __future__ import annotations

import typing as t
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .base import Injectable


class AuthorizationError(Exception):
    """Base class for framework-agnostic authorization failures."""


class AuthenticationRequired(AuthorizationError):
    """Raised when an anonymous actor must authenticate first."""


class PermissionDenied(AuthorizationError):
    """Raised when the current actor is not allowed to perform an action."""


TValue = t.TypeVar("TValue")


class BasePolicy(Injectable):
    """Base class for reusable, injectable authorization policies."""

    @staticmethod
    def require_allowed(is_allowed: bool, reason: str | None = None) -> None:
        """Raise when the provided decision denies the action."""
        if not is_allowed:
            raise PermissionDenied(reason or "Permission denied")

    @staticmethod
    def require_authenticated(
        is_authenticated: bool,
        reason: str | None = None,
    ) -> None:
        """Raise when an action requires an authenticated actor."""
        if not is_authenticated:
            raise AuthenticationRequired(reason or "Authentication required")


class AuthorizationResult(ABC, t.Generic[TValue]):
    """Base class for allowed-or-denied authorization results."""

    @abstractmethod
    def require(self) -> Allow[TValue]:
        """Return the allowed result or raise if denied."""


@dataclass(frozen=True, slots=True)
class Allow(AuthorizationResult[TValue]):
    """An allowed authorization result with a query-transform callback."""

    apply: t.Callable[[TValue], TValue]

    def require(self) -> Allow[TValue]:
        """Return the allowed result unchanged."""
        return self

    def __call__(self, value: TValue) -> TValue:
        """Apply the stored callback to a value."""
        return self.apply(value)


@dataclass(frozen=True, slots=True)
class Deny(AuthorizationResult[TValue]):
    """A denied authorization result with a rejection reason."""

    reason: str

    def require(self) -> t.NoReturn:
        """Raise when the result denies the requested action."""
        raise PermissionDenied(self.reason)
