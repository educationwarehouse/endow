from __future__ import annotations

import abc

import pytest
from src.endow import BackendBase, Domain, Injectable, Service
from src.endow.runtime import build_graph


class Db:
    def __init__(self) -> None:
        self.events: list[str] = []


class Counter(Injectable):
    value: int


class Applog(Service):
    db: Db

    @classmethod
    def from_env(cls, db: Db) -> Applog:
        return cls()

    def track(self, event: str) -> None:
        self.db.events.append(event)


class Mailer(Service, abc.ABC):
    applog: Applog
    db: Db

    @classmethod
    def from_env(cls, db: Db) -> Mailer:
        if db.events and db.events[0] == "use-fake":
            return FakeMailer.from_env(db=db)
        return SMTPMailer.from_env(db=db)

    @abc.abstractmethod
    def send(self, recipient: str, subject: str, body: str) -> None:
        raise NotImplementedError


class SMTPMailer(Mailer):
    @classmethod
    def from_env(cls, db: Db) -> SMTPMailer:
        return cls()

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(f"smtp:{recipient}:{subject}:{body}")


class FakeMailer(Mailer):
    @classmethod
    def from_env(cls, db: Db) -> FakeMailer:
        return cls()

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(f"fake:{recipient}:{subject}:{body}")


class Products(Domain):
    methods: Methods
    mailer: Mailer
    applog: Applog
    counter: Counter

    def update(self, product_id: int) -> None:
        self.counter.value += 1
        self.applog.track(f"products:{product_id}:{self.counter.value}")
        self.mailer.send("ops@example.com", "updated", str(product_id))


class Methods(Domain):
    products: Products
    applog: Applog

    def update(self, method_id: int, product_id: int) -> None:
        self.applog.track(f"methods:{method_id}")
        self.products.update(product_id)


class AppBackend(BackendBase):
    products: Products
    methods: Methods


class UnderscorePrefixedApplog(Service):
    db: Db

    @classmethod
    def from_env(cls, _db: Db) -> UnderscorePrefixedApplog:
        return cls()


class BareUnderscoreApplog(Service):
    db: Db

    @classmethod
    def from_env(cls, _: Db) -> BareUnderscoreApplog:
        assert _, "unnamed variable should still have value"
        return cls()


class BadFactory(Service):
    @classmethod
    def from_env(cls) -> object:
        return object()


class NeedsRuntimeValue(Service):
    db: Db


class UnconstructableValue:
    def __init__(self, required: str) -> None:
        self.required = required


class NeedsUnconstructableField(Service):
    value: UnconstructableValue


class NeedsUnsupportedAnnotation(Service):
    value: int | str


class DefaultedFactory(Service):
    db: Db
    label: str

    @classmethod
    def from_env(cls, db: Db, label: str = "default") -> DefaultedFactory:
        return cls()


class VariadicFactory(Service):
    db: Db

    @classmethod
    def from_env(cls, db: Db, *args: object, **kwargs: object) -> VariadicFactory:
        return cls()


class UnannotatedFactory(Service):
    db: Db

    @classmethod
    def from_env(cls, db) -> UnannotatedFactory:
        return cls()


class NamedDefaultDoesNotOverrideTypedInput(Service):
    value: str

    @classmethod
    def from_env(cls, label: str = "default") -> NamedDefaultDoesNotOverrideTypedInput:
        return cls()


def test_builds_a_shared_graph_per_root() -> None:
    backend = AppBackend.from_env(db=Db())

    assert backend.products is backend.methods.products
    assert backend.products.applog is backend.methods.applog
    assert backend.products.counter is backend.products.counter


def test_separate_roots_are_isolated() -> None:
    first = AppBackend.from_env(db=Db())
    second = AppBackend.from_env(db=Db())

    assert first is not second
    assert first.products is not second.products
    assert first.products.applog is not second.products.applog


def test_direct_cycles_work_without_wrapper_types() -> None:
    backend = AppBackend.from_env(db=Db())

    assert backend.products.methods is backend.methods
    assert backend.methods.products is backend.products


def test_factory_selection_can_return_a_concrete_subclass() -> None:
    db = Db()
    backend = AppBackend.from_env(db=db)
    assert isinstance(backend.products.mailer, SMTPMailer)

    fake_db = Db()
    fake_db.events.append("use-fake")
    fake_backend = AppBackend.from_env(db=fake_db)
    assert isinstance(fake_backend.products.mailer, FakeMailer)


def test_runtime_input_flows_to_nested_factories() -> None:
    db = Db()
    backend = AppBackend.from_env(db=db)

    assert backend.products.applog.db is db
    assert backend.products.mailer.db is db


def test_graph_is_executable_after_wiring() -> None:
    db = Db()
    backend = AppBackend.from_env(db=db)

    backend.methods.update(method_id=2, product_id=7)

    assert db.events == [
        "methods:2",
        "products:7:1",
        "smtp:ops@example.com:updated:7",
    ]


def test_missing_runtime_input_raises_a_clear_error() -> None:
    with pytest.raises(TypeError):
        AppBackend.from_env()


def test_factory_accepts_underscore_prefixed_runtime_input_names() -> None:
    db = Db()

    applog = build_graph(UnderscorePrefixedApplog, {"db": db})

    assert applog.db is db


def test_factory_accepts_bare_underscore_runtime_input_names() -> None:
    db = Db()

    applog = build_graph(BareUnderscoreApplog, {"db": db})

    assert applog.db is db


def test_factory_return_must_be_injectable() -> None:
    with pytest.raises(TypeError, match="BadFactory.from_env\\(\\) must return an Injectable instance"):
        build_graph(BadFactory, {})


def test_missing_runtime_input_for_unconstructable_field_raises_clear_error() -> None:
    with pytest.raises(TypeError, match="Missing runtime input for field 'value'"):
        build_graph(NeedsUnconstructableField, {})


def test_unsupported_annotation_raises_clear_error() -> None:
    with pytest.raises(TypeError, match="Cannot resolve field 'value'"):
        build_graph(NeedsUnsupportedAnnotation, {})


def test_factory_defaults_are_used_when_runtime_input_is_missing() -> None:
    db = Db()

    # service = build_graph(DefaultedFactory, {"db": db})
    service = DefaultedFactory.build(db=db)

    assert service.db is db
    assert service.label == "default"


def test_variadic_factory_parameters_are_ignored() -> None:
    db = Db()

    service = build_graph(VariadicFactory, {"db": db})

    assert service.db is db


def test_unannotated_factory_parameter_requires_named_runtime_input() -> None:
    with pytest.raises(TypeError, match="Missing runtime input 'db' for from_env\\(\\)"):
        build_graph(UnannotatedFactory, {"database": Db()})


def test_type_based_runtime_inputs_override_local_factory_defaults() -> None:
    service = build_graph(NamedDefaultDoesNotOverrideTypedInput, {"runtime_value": "from-runtime"})

    assert service.value == "from-runtime"
