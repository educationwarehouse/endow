from __future__ import annotations

import inspect
import sys
import typing as t

from .base import Injectable

MISSING = object()


class Graph:
    def __init__(self, runtime_inputs: dict[str, t.Any]) -> None:
        self.runtime_inputs = runtime_inputs
        self.instances: dict[type[Injectable], Injectable] = {}

    def build[T: Injectable](self, cls: type[T]) -> T:
        cached = self.instances.get(cls)
        if cached is not None:
            return t.cast(T, cached)

        instance, local_inputs = self._make_instance(cls)
        self.instances[cls] = instance
        self.instances.setdefault(type(instance), instance)
        self._wire_instance(instance, local_inputs=local_inputs)
        return t.cast(T, instance)

    def _make_instance[T: Injectable](self, cls: type[T]) -> tuple[Injectable, dict[str, t.Any] | None]:
        factory = self._get_factory(cls)
        if factory is None:
            return cls(), None

        factory_args, local_inputs = self._collect_factory_args(factory)
        instance = factory(**factory_args)
        if not isinstance(instance, Injectable):
            msg = f"{cls.__name__}.from_env() must return an Injectable instance"
            raise TypeError(msg)
        return instance, local_inputs

    def _wire_instance(self, instance: Injectable, local_inputs: dict[str, t.Any] | None = None) -> None:
        for name, annotation in iter_injected_fields(type(instance)).items():
            setattr(instance, name, self._resolve(name, annotation, local_inputs))

    def _resolve(self, name: str, annotation: t.Any, local_inputs: dict[str, t.Any] | None = None) -> t.Any:
        if inspect.isclass(annotation) and issubclass(annotation, Injectable):
            return self.build(annotation)

        runtime_value = self._match_runtime_input(name, annotation, local_inputs)
        if runtime_value is not MISSING:
            return runtime_value

        if inspect.isclass(annotation):
            try:
                return annotation()
            except TypeError as exc:
                msg = f"Missing runtime input for field '{name}'"
                raise TypeError(msg) from exc

        msg = f"Cannot resolve field '{name}' with annotation {annotation!r}"
        raise TypeError(msg)

    def _collect_factory_args(self, factory: t.Any) -> tuple[dict[str, t.Any], dict[str, t.Any]]:
        args: dict[str, t.Any] = {}
        local_inputs: dict[str, t.Any] = {}
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

        return args, local_inputs

    def _match_runtime_input(
        self,
        name: str,
        annotation: t.Any,
        local_inputs: dict[str, t.Any] | None = None,
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

        for scope in (self.runtime_inputs, local_inputs):
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


def build_graph[T: Injectable](cls: type[T], runtime_inputs: dict[str, t.Any]) -> T:
    graph = Graph(runtime_inputs)
    return graph.build(cls)


def iter_injected_fields(cls: type[Injectable]) -> dict[str, t.Any]:
    fields: dict[str, t.Any] = {}

    for base in reversed(cls.__mro__):
        if not issubclass(base, Injectable) or base is Injectable:
            continue

        module = sys.modules[base.__module__]
        fields.update(t.get_type_hints(base, globalns=vars(module)))

    return fields


def matches_annotation(value: t.Any, annotation: t.Any) -> bool:
    return inspect.isclass(annotation) and isinstance(value, annotation)
