"""Base injectable types used by the dependency graph runtime."""

from __future__ import annotations

import typing as t


class Injectable:
    """Base class for objects that participate in endow graph wiring."""

    @classmethod
    def with_injected(cls, **kw: t.Any) -> t.Self:
        """Build an injectable instance using the runtime graph."""
        from .runtime import build_graph

        return build_graph(cls, kw)

    @classmethod
    def build(cls, **kw: t.Any) -> t.Self:
        """Backward-compatible alias for building an injected instance."""
        return cls.with_injected(**kw)


class Service(Injectable):
    """Infrastructure capability resolved by the runtime."""


class Domain(Injectable):
    """Domain component resolved by the runtime."""
