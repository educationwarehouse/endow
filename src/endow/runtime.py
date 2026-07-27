"""Runtime graph construction and wiring helpers."""

import annotationlib
import inspect
import sys
import typing as t
import warnings
from dataclasses import dataclass

from .base import Domain, Injectable, Service

MISSING = object()


@dataclass(frozen=True)
class RuntimeRequirement:
    """A runtime value needed to construct an injectable graph."""

    name: str
    annotation: t.Any
    owner: type[Injectable]
    path: tuple[str, ...]
    source: t.Literal["field", "from_env"]

    @property
    def description(self) -> str:
        """Return the requirement in the same form as runtime diagnostics."""
        if self.annotation is inspect._empty:
            return self.name
        return f"{self.name}: {annotation_name(self.annotation)}"


class RuntimeInputAuditError(TypeError):
    """Raised when declared runtime inputs do not satisfy a graph's requirements."""


class Graph:
    """Resolve injectables and runtime values into a shared object graph."""

    def __init__(self, runtime_inputs: dict[str, t.Any], strict: bool | None = None) -> None:
        """Store the root runtime inputs and initialize the instance cache."""
        self.runtime_inputs = runtime_inputs
        self.strict = strict
        self.instances: dict[type[Injectable], Injectable] = {}

    def build[T: Injectable](self, cls: type[T], path: tuple[str, ...] = ()) -> T:
        """Build or reuse an injectable instance of the requested type."""
        cached = self.instances.get(cls)
        if cached is not None:
            return t.cast(T, cached)

        compatible = self._find_compatible_instance(cls)
        if compatible is not None:
            self.instances[cls] = compatible
            return t.cast(T, compatible)

        instance, local_inputs, type_inputs = self._make_instance(cls, path)
        self.instances[cls] = instance
        self.instances.setdefault(type(instance), instance)
        self._wire_instance(instance, local_inputs=local_inputs, type_inputs=type_inputs, path=path)
        return t.cast(T, instance)

    def _find_compatible_instance[T: Injectable](self, cls: type[T]) -> Injectable | None:
        matches: list[Injectable] = []
        seen_ids: set[int] = set()

        for candidate in self.instances.values():
            if id(candidate) in seen_ids:
                continue
            seen_ids.add(id(candidate))
            if isinstance(candidate, cls):
                matches.append(candidate)

        if not matches:
            return None

        if len(matches) > 1:
            msg = f"Multiple cached instances satisfy injectable base '{cls.__name__}'"
            raise TypeError(msg)

        return matches[0]

    def _make_instance[T: Injectable](
        self, cls: type[T], path: tuple[str, ...]
    ) -> tuple[Injectable, dict[str, t.Any] | None, dict[str, t.Any] | None]:
        factory = self._get_factory(cls)
        if factory is None:
            return cls(), None, None

        factory_args, local_inputs, type_inputs = self._collect_factory_args(factory, cls, path)
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
        path: tuple[str, ...] = (),
    ) -> None:
        owner_type = type(instance)
        for name, annotation in iter_injected_fields(owner_type).items():
            if inspect.getattr_static(instance, name, MISSING) is not MISSING:
                continue
            self._check_dependency_direction(owner_type, name, annotation)
            setattr(instance, name, self._resolve(instance, name, annotation, local_inputs, type_inputs, path))

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
        path: tuple[str, ...] = (),
    ) -> t.Any | None:
        if name.startswith("_"):
            return None

        runtime_value = self._match_runtime_input(name, annotation, local_inputs, type_inputs)
        if runtime_value is not MISSING:
            return runtime_value

        if inspect.isclass(annotation) and issubclass(annotation, Injectable):
            return self.build(annotation, (*path, format_path_segment(type(instance), name)))

        if inspect.isclass(annotation):
            msg = f"Missing runtime input for field '{name}: {annotation.__name__}' in {instance.__class__.__name__}"
            raise TypeError(f"{msg}; dependency path: {format_path(path, type(instance), name)}")

        if isinstance(annotation, annotationlib.ForwardRef):
            resolved_annotation = annotation.evaluate(
                locals=locals(),
                globals=globals() | Injectable.get_known_injectables(),
                type_params=(),
            )
            return self._resolve(instance, name, resolved_annotation, local_inputs, type_inputs, path)

        msg = f"Cannot resolve field '{name}' with annotation {annotation!r}"
        raise TypeError(msg)

    def _collect_factory_args(
        self, factory: t.Any, cls: type[Injectable], path: tuple[str, ...]
    ) -> tuple[dict[str, t.Any], dict[str, t.Any], dict[str, t.Any]]:
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
                raise TypeError(f"{msg}; dependency path: {format_factory_path(path, cls, parameter.name)}")
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


def inspect_runtime_requirements(cls: type[Injectable]) -> tuple[RuntimeRequirement, ...]:
    """Return the declared runtime inputs needed to construct ``cls`` without constructing it."""
    requirements: list[RuntimeRequirement] = []
    requirement_keys: set[tuple[str, t.Any]] = set()
    visited: set[type[Injectable]] = set()

    def add_requirement(requirement: RuntimeRequirement) -> None:
        key = (requirement.name, requirement.annotation)
        if key in requirement_keys:
            return
        requirement_keys.add(key)
        requirements.append(requirement)

    def visit(owner: type[Injectable], path: tuple[str, ...]) -> None:
        if owner in visited:
            return
        visited.add(owner)

        factory = Graph._get_factory(owner)
        if factory is not None:
            for parameter in inspect.signature(factory).parameters.values():
                if (
                    parameter.kind
                    not in (
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    )
                    or parameter.default is not inspect._empty
                ):
                    continue
                add_requirement(
                    RuntimeRequirement(
                        name=parameter.name,
                        annotation=get_factory_annotation(factory, parameter),
                        owner=owner,
                        path=(*path, format_factory_path((), owner, parameter.name)),
                        source="from_env",
                    )
                )

        for name, annotation in iter_injected_fields(owner).items():
            if name.startswith("_"):
                continue
            annotation = resolve_forward_ref(annotation)
            field_path = (*path, format_path_segment(owner, name))
            if inspect.isclass(annotation) and issubclass(annotation, Injectable):
                visit(annotation, field_path)
                continue
            if factory is not None:
                continue
            if inspect.isclass(annotation):
                add_requirement(
                    RuntimeRequirement(
                        name=name,
                        annotation=annotation,
                        owner=owner,
                        path=field_path,
                        source="field",
                    )
                )

    visit(cls, ())
    return tuple(requirements)


def validate_runtime_inputs(cls: type[Injectable], runtime_inputs: dict[str, t.Any]) -> None:
    """Raise an audit error when ``runtime_inputs`` cannot satisfy all declared requirements."""
    missing = tuple(
        requirement
        for requirement in inspect_runtime_requirements(cls)
        if not runtime_input_matches(requirement, runtime_inputs)
    )
    if not missing:
        return

    details = "\n".join(
        f"- {requirement.description} required by {requirement.owner.__name__} (path: {' -> '.join(requirement.path)})"
        for requirement in missing
    )
    raise RuntimeInputAuditError(f"Missing runtime inputs for {cls.__name__}:\n{details}")


def get_factory_annotation(factory: t.Any, parameter: inspect.Parameter) -> t.Any:
    """Return a resolved factory parameter annotation when one is available."""
    module = sys.modules[factory.__module__]
    type_hints = t.get_type_hints(factory, globalns=vars(module))
    return type_hints.get(parameter.name, parameter.annotation)


def resolve_forward_ref(annotation: t.Any) -> t.Any:
    """Resolve an injectable forward reference when its target is known."""
    if not isinstance(annotation, annotationlib.ForwardRef):
        return annotation
    return annotation.evaluate(
        locals=locals(),
        globals=globals() | Injectable.get_known_injectables(),
        type_params=(),
    )


def annotation_name(annotation: t.Any) -> str:
    """Return a readable annotation name for diagnostics."""
    return getattr(annotation, "__name__", repr(annotation))


def format_path_segment(owner: type[Injectable], name: str) -> str:
    """Return a dependency path segment for an injected field."""
    return f"{owner.__name__}.{name}"


def format_factory_path(path: tuple[str, ...], owner: type[Injectable], name: str) -> str:
    """Return a dependency path ending at a factory parameter."""
    return " -> ".join((*path, f"{owner.__name__}.from_env({name})"))


def format_path(path: tuple[str, ...], owner: type[Injectable], name: str) -> str:
    """Return a dependency path ending at an injected field."""
    return " -> ".join((*path, format_path_segment(owner, name)))


def runtime_input_matches(requirement: RuntimeRequirement, runtime_inputs: dict[str, t.Any]) -> bool:
    """Return whether the inputs satisfy a requirement using runtime resolution semantics."""
    normalized_name = requirement.name.lstrip("_")
    if requirement.name in runtime_inputs or (normalized_name and normalized_name in runtime_inputs):
        return True
    return requirement.annotation is not inspect._empty and any(
        matches_annotation(value, requirement.annotation) for value in runtime_inputs.values()
    )


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
