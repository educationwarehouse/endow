"""Runtime graph construction and wiring helpers."""

from __future__ import annotations

import inspect
import sys
import typing as t
import warnings

import annotationlib

from .base import Domain, Injectable, Service

MISSING = object()


class Graph:
    """Resolve injectables and runtime values into a shared object graph."""

    def __init__(self, runtime_inputs: dict[str, t.Any], strict: bool | None = None) -> None:
        """Store the root runtime inputs and initialize the instance cache."""
        self.runtime_inputs = runtime_inputs
        self.strict = strict
        self.instances: dict[type[Injectable], Injectable] = {}

    def build[T: Injectable](self, cls: type[T]) -> T:
        """Build or reuse an injectable instance of the requested type."""
        cached = self.instances.get(cls)
        if cached is not None:
            return t.cast(T, cached)

        instance, local_inputs, type_inputs = self._make_instance(cls)
        self.instances[cls] = instance
        self.instances.setdefault(type(instance), instance)
        self._wire_instance(instance, local_inputs=local_inputs, type_inputs=type_inputs)
        return t.cast(T, instance)

    def _make_instance[T: Injectable](
        self, cls: type[T]
    ) -> tuple[Injectable, dict[str, t.Any] | None, dict[str, t.Any] | None]:
        factory = self._get_factory(cls)
        if factory is None:
            return cls(), None, None

        factory_args, local_inputs, type_inputs = self._collect_factory_args(factory)
        instance = factory(**factory_args)
        if not isinstance(instance, Injectable):
            msg = f"{cls.__name__}.from_env() must return an Injectable instance"
            raise TypeError(msg)
        return instance, local_inputs, type_inputs

    def _wire_instance(
        self,
        instance: Injectable,
        local_inputs: dict[str, t.Any] | None = None,
        type_inputs: dict[str, t.Any] | None = None,
    ) -> None:
        owner_type = type(instance)
        for name, annotation in iter_injected_fields(owner_type).items():
            if inspect.getattr_static(instance, name, MISSING) is not MISSING:
                continue
            self._check_dependency_direction(owner_type, name, annotation)
            setattr(instance, name, self._resolve(instance, name, annotation, local_inputs, type_inputs))

    def _check_dependency_direction(self, owner_type: type[Injectable], name: str, annotation: t.Any) -> None:
        if self.strict is None:
            return

        if not (
            inspect.isclass(owner_type)
            and issubclass(owner_type, Service)
            and inspect.isclass(annotation)
            and issubclass(annotation, Domain)
        ):
            return

        msg = f"Service '{owner_type.__name__}' should not depend on Domain '{annotation.__name__}' via field '{name}'"
        if self.strict:
            raise TypeError(msg)
        warnings.warn(msg, stacklevel=4)

    def _resolve(
        self,
        instance: Injectable,
        name: str,
        annotation: t.Any,
        local_inputs: dict[str, t.Any] | None = None,
        type_inputs: dict[str, t.Any] | None = None,
        skip_private: bool = True,
    ) -> t.Any | None:
        if name.startswith("_"):
            return None

        if inspect.isclass(annotation) and issubclass(annotation, Injectable):
            return self.build(annotation)

        runtime_value = self._match_runtime_input(name, annotation, local_inputs, type_inputs)
        if runtime_value is not MISSING:
            return runtime_value

        if inspect.isclass(annotation):
            msg = f"Missing runtime input for field '{name}: {annotation.__name__}' in {instance.__class__.__name__}"
            raise TypeError(msg)

        if isinstance(annotation, annotationlib.ForwardRef):
            resolved_annotation = annotation.evaluate(
                locals=locals(),
                globals=globals() | Injectable.get_known_injectables(),
                type_params=(),
            )
            return self._resolve(instance, name, resolved_annotation, local_inputs, type_inputs, skip_private)

        msg = f"Cannot resolve field '{name}' with annotation {annotation!r}"
        raise TypeError(msg)

    def _collect_factory_args(self, factory: t.Any) -> tuple[dict[str, t.Any], dict[str, t.Any], dict[str, t.Any]]:
        args: dict[str, t.Any] = {}
        local_inputs: dict[str, t.Any] = {}
        type_inputs: dict[str, t.Any] = {}
        module = sys.modules[factory.__module__]
        type_hints = t.get_type_hints(factory, globalns=vars(module))

        for parameter in inspect.signature(factory).parameters.values():
            if parameter.kind not in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                continue

            annotation = type_hints.get(parameter.name, parameter.annotation)
            value = self._match_runtime_input(parameter.name, annotation)
            if value is MISSING:
                if parameter.default is not inspect._empty:
                    local_inputs[parameter.name] = parameter.default
                    continue
                msg = f"Missing runtime input '{parameter.name}' for from_env()"
                raise TypeError(msg)
            args[parameter.name] = value
            local_inputs[parameter.name] = value
            type_inputs[parameter.name] = value

        return args, local_inputs, type_inputs

    def _match_runtime_input(
        self,
        name: str,
        annotation: t.Any,
        local_inputs: dict[str, t.Any] | None = None,
        type_inputs: dict[str, t.Any] | None = None,
    ) -> t.Any:
        for scope in (local_inputs, self.runtime_inputs):
            if not scope:
                continue
            if name in scope:
                return scope[name]

            normalized_name = name.lstrip("_")
            if normalized_name and normalized_name in scope:
                return scope[normalized_name]

        if annotation is inspect._empty:
            return MISSING

        for scope in (self.runtime_inputs, type_inputs):
            if not scope:
                continue
            for candidate in scope.values():
                if matches_annotation(candidate, annotation):
                    return candidate

        return MISSING

    @staticmethod
    def _get_factory(cls: type[Injectable]) -> t.Any | None:
        from .backend import BackendBase

        for base in cls.__mro__:
            descriptor = base.__dict__.get("from_env")
            if descriptor is None:
                continue
            if base is BackendBase:
                continue
            return descriptor.__get__(None, cls)
        return None


def build_graph[T: Injectable](
    cls: type[T],
    runtime_inputs: dict[str, t.Any],
    strict: bool | None = None,
) -> T:
    """Build a dependency graph rooted at ``cls``."""
    graph = Graph(runtime_inputs, strict=strict)
    return graph.build(cls)


def get_annotations(base: type) -> dict[str, type]:
    """Get type hints, using `annotationlib.Format.FORWARDREF` to defer missing types."""
    module = sys.modules[base.__module__]

    # if reference doesn't exist, don't crash here. Rather let Graph._resolve deal with it (e.g decide to skip/raise)
    return t.get_type_hints(base, globalns=vars(module), format=annotationlib.Format.FORWARDREF)


def iter_injected_fields(cls: type[Injectable]) -> dict[str, t.Any]:
    """Return annotated injected fields declared on ``cls`` and its bases."""
    fields: dict[str, t.Any] = {}

    for base in reversed(cls.__mro__):
        if not issubclass(base, Injectable) or base is Injectable:
            continue

        fields.update(get_annotations(base))

    return fields


def matches_annotation(value: t.Any, annotation: t.Any) -> bool:
    """Return whether ``value`` matches a concrete class annotation."""
    return inspect.isclass(annotation) and isinstance(value, annotation)
