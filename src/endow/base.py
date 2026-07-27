"""Base injectable types used by the dependency graph runtime."""

import typing as t

if t.TYPE_CHECKING:
    from .runtime import RuntimeRequirement


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
    def runtime_requirements(cls) -> tuple[RuntimeRequirement, ...]:
        """Return declared runtime inputs without constructing the graph."""
        from .runtime import inspect_runtime_requirements

        return inspect_runtime_requirements(cls)

    @classmethod
    def validate_runtime_inputs(cls, **runtime_inputs: t.Any) -> None:
        """Raise when runtime inputs do not satisfy the declared graph requirements."""
        from .runtime import validate_runtime_inputs

        validate_runtime_inputs(cls, runtime_inputs)

    @classmethod
    def get_known_injectables(cls) -> dict[str, type]:
        """Return a dictionary of known injectable types in the class."""
        return dict(cls._KNOWN_INJECTABLES)

    def close(self) -> None:
        """Disconnect injectable references held by this backend's object graph."""
        pending = [self]
        instances: list[Injectable] = []
        seen_ids: set[int] = set()

        while pending:
            instance = pending.pop()
            if id(instance) in seen_ids:
                continue
            seen_ids.add(id(instance))
            instances.append(instance)
            pending.extend(value for value in vars(instance).values() if isinstance(value, Injectable))

        for instance in instances:
            for name, value in vars(instance).copy().items():
                if isinstance(value, Injectable):
                    delattr(instance, name)


class Service(Injectable):
    """Infrastructure capability resolved by the runtime."""


class Domain(Injectable):
    """Domain component resolved by the runtime."""
