"""Base injectable types used by the dependency graph runtime."""

from __future__ import annotations

import typing as t


class Injectable:
    """Base class for objects that participate in endow graph wiring."""

    _KNOWN_INJECTABLES: t.ClassVar[t.MutableMapping[str, type]] = {}

    def __init_subclass__(cls, **_: t.Any) -> None:
        """Register new subclass for resolving."""
        Injectable._KNOWN_INJECTABLES[cls.__qualname__] = cls

    @classmethod
    def with_injected(cls, **kw: t.Any) -> t.Self:
        """Build an injectable instance using the runtime graph."""
        from .runtime import build_graph

        return build_graph(cls, kw)

    @classmethod
    def build(cls, **kw: t.Any) -> t.Self:
        """Backward-compatible alias for building an injected instance."""
        return cls.with_injected(**kw)

    @classmethod
    def get_known_injectables(cls) -> dict[str, type]:
        """Return a dictionary of known injectable types in the class."""
        return dict(cls._KNOWN_INJECTABLES)


class Service(Injectable):
    """Infrastructure capability resolved by the runtime."""


class Domain(Injectable):
    """Domain component resolved by the runtime."""
