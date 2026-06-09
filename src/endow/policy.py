"""Framework-agnostic policy primitives for authorization-aware domains."""

from __future__ import annotations

from dataclasses import dataclass

from .base import Injectable


class AuthorizationError(Exception):
    """Base class for framework-agnostic authorization failures."""


class AuthenticationRequired(AuthorizationError):
    """Raised when an anonymous actor must authenticate first."""


class PermissionDenied(AuthorizationError):
    """Raised when the current actor is not allowed to perform an action."""


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """A reusable allow-or-deny policy decision."""

    allowed: bool
    reason: str | None = None

    def require(self) -> None:
        """Raise when the decision denies the requested action."""
        if not self.allowed:
            raise PermissionDenied(self.reason or "Permission denied")


class BasePolicy(Injectable):
    """Base class for reusable, injectable authorization policies."""

    @staticmethod
    def allow(reason: str | None = None) -> AuthorizationDecision:
        """Return an allowing decision."""
        return AuthorizationDecision(allowed=True, reason=reason)

    @staticmethod
    def deny(reason: str | None = None) -> AuthorizationDecision:
        """Return a denying decision."""
        return AuthorizationDecision(allowed=False, reason=reason)

    @staticmethod
    def require_allowed(decision: AuthorizationDecision | bool, reason: str | None = None) -> None:
        """Raise when the provided decision denies the action."""
        if isinstance(decision, bool):
            decision = AuthorizationDecision(allowed=decision, reason=reason)
        decision.require()

    @staticmethod
    def require_authenticated(
        is_authenticated: bool,
        reason: str | None = None,
    ) -> None:
        """Raise when an action requires an authenticated actor."""
        if not is_authenticated:
            raise AuthenticationRequired(reason or "Authentication required")
