import abc
from typing import Protocol, runtime_checkable

import pytest
from src.endow import BackendBase, Domain, Injectable, Service

class Db:
    def __init__(self) -> None:
        self.events: list[str] = []


@runtime_checkable
class AuthContext(Protocol):
    def can(self, permission: str) -> bool: ...


class StaticAuth:
    def __init__(self, allowed_permissions: set[str]) -> None:
        self.allowed_permissions = allowed_permissions

    def can(self, permission: str) -> bool:
        return permission in self.allowed_permissions


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

    sender: str
    reply_to: str = "noreply@default"

    @classmethod
    def from_env(cls, db: Db) -> Mailer:
        if db.events and db.events[0] == "use-fake":
            return FakeMailer.from_env(db=db)
        return SMTPMailer.from_env(db=db)

    @abc.abstractmethod
    def send(self, recipient: str, subject: str, body: str) -> None:
        raise NotImplementedError


class SMTPMailer(Mailer):
    def __init__(self):
        super().__init__()
        self.sender = "from@smtp"

    @classmethod
    def from_env(cls, db: Db) -> SMTPMailer:
        return cls()

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(f"smtp:{recipient}:{subject}:{body}")


class FakeMailer(Mailer):
    def __init__(self):
        super().__init__()
        self.sender = "mock@internal"

    @classmethod
    def from_env(cls, db: Db) -> FakeMailer:
        return cls()

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(f"fake:{recipient}:{subject}:{body}")


class Products(Domain):
    auth: AuthContext
    methods: Methods
    mailer: Mailer
    applog: Applog
    counter: Counter

    def update(self, product_id: int) -> None:
        if not self.auth.can("products.update"):
            msg = "missing products.update permission"
            raise PermissionError(msg)
        self.counter.value += 1
        self.applog.track(f"products:{product_id}:{self.counter.value}")
        self.mailer.send("ops@example.com", "updated", str(product_id))


class Methods(Domain):
    products: Products
    applog: Applog

    def update(self, method_id: int, product_id: int) -> None:
        self.applog.track(f"methods:{method_id}")
        self.products.update(product_id)


class Reports(Service):
    products: Products


class AppBackend(BackendBase):
    products: Products
    methods: Methods
    reports: Reports


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


class DefaultConstructableDb(Service):
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
    backend = AppBackend.with_injected(db=Db(), value=0, auth=StaticAuth({"products.update"}))

    assert backend.products is backend.methods.products
    assert backend.products.applog is backend.methods.applog
    assert backend.products.counter is backend.products.counter


def test_separate_roots_are_isolated() -> None:
    auth = StaticAuth({"products.update"})
    first = AppBackend.with_injected(db=Db(), value=0, auth=auth)
    second = AppBackend.with_injected(db=Db(), value=0, auth=auth)

    assert first is not second
    assert first.products is not second.products
    assert first.products.applog is not second.products.applog


def test_backend_from_env_alias_matches_with_injected() -> None:
    backend = AppBackend.from_env(db=Db(), value=0, auth=StaticAuth({"products.update"}))

    assert isinstance(backend.products, Products)
    assert isinstance(backend.methods, Methods)


def test_direct_cycles_work_without_wrapper_types() -> None:
    backend = AppBackend.with_injected(db=Db(), value=0, auth=StaticAuth({"products.update"}))

    assert backend.products.methods is backend.methods
    assert backend.methods.products is backend.products


def test_factory_selection_can_return_a_concrete_subclass() -> None:
    db = Db()
    auth = StaticAuth({"products.update"})
    backend = AppBackend.with_injected(db=db, value=0, auth=auth)
    assert isinstance(backend.products.mailer, SMTPMailer)

    fake_db = Db()
    fake_db.events.append("use-fake")
    fake_backend = AppBackend.with_injected(db=fake_db, value=0, auth=auth)
    assert isinstance(fake_backend.products.mailer, FakeMailer)


def test_runtime_input_flows_to_nested_factories() -> None:
    db = Db()
    backend = AppBackend.with_injected(db=db, value=0, auth=StaticAuth({"products.update"}))

    assert backend.products.applog.db is db
    assert backend.products.mailer.db is db


def test_domain_with_injected_builds_a_standalone_graph() -> None:
    db = Db()
    auth = StaticAuth({"products.update"})

    products = Products.with_injected(db=db, value=0, auth=auth)

    assert isinstance(products, Products)
    assert products.methods.products is products
    assert products.applog.db is db
    assert products.auth is auth


def test_service_with_injected_builds_a_standalone_graph() -> None:
    db = Db()

    applog = Applog.with_injected(db=db)

    assert isinstance(applog, Applog)
    assert applog.db is db


def test_graph_is_executable_after_wiring() -> None:
    db = Db()
    backend = AppBackend.with_injected(db=db, value=0, auth=StaticAuth({"products.update"}))

    backend.methods.update(method_id=2, product_id=7)

    assert db.events == [
        "methods:2",
        "products:7:1",
        "smtp:ops@example.com:updated:7",
    ]


def test_missing_runtime_input_raises_a_clear_error() -> None:
    with pytest.raises(TypeError, match="Missing runtime input for field 'auth: AuthContext'"):
        AppBackend.with_injected()


def test_protocol_typed_auth_runtime_input_is_injected() -> None:
    auth = StaticAuth({"products.update"})

    backend = AppBackend.with_injected(db=Db(), value=0, auth=auth)

    assert backend.products.auth is auth


def test_missing_runtime_input_for_zero_arg_class_field_raises_clear_error() -> None:
    with pytest.raises(TypeError, match="Missing runtime input for field 'db: Db'"):
        DefaultConstructableDb.with_injected()


def test_factory_accepts_underscore_prefixed_runtime_input_names() -> None:
    db = Db()

    applog = UnderscorePrefixedApplog.with_injected(db=db)

    assert applog.db is db


def test_factory_accepts_bare_underscore_runtime_input_names() -> None:
    db = Db()

    applog = BareUnderscoreApplog.with_injected(db=db)

    assert applog.db is db


def test_from_env_checked_warns_on_service_to_domain_dependencies() -> None:
    with pytest.warns(UserWarning, match="Service 'Reports' should not depend on Domain 'Products'"):
        backend = AppBackend.with_injected_checked(
            strict=False,
            db=Db(),
            value=0,
            auth=StaticAuth({"products.update"}),
        )

    assert backend.reports.products is backend.products


def test_from_env_checked_alias_warns_on_service_to_domain_dependencies() -> None:
    with pytest.warns(UserWarning, match="Service 'Reports' should not depend on Domain 'Products'"):
        backend = AppBackend.from_env_checked(
            strict=False,
            db=Db(),
            value=0,
            auth=StaticAuth({"products.update"}),
        )

    assert backend.reports.products is backend.products


def test_from_env_checked_can_fail_on_service_to_domain_dependencies() -> None:
    with pytest.raises(
        TypeError,
        match="Service 'Reports' should not depend on Domain 'Products' via field 'products'",
    ):
        AppBackend.with_injected_checked(
            strict=True,
            db=Db(),
            value=0,
            auth=StaticAuth({"products.update"}),
        )


def test_factory_return_must_be_injectable() -> None:
    with pytest.raises(TypeError, match="BadFactory.from_env\\(\\) must return an Injectable instance"):
        BadFactory.with_injected()


def test_missing_runtime_input_for_unconstructable_field_raises_clear_error() -> None:
    with pytest.raises(TypeError, match="Missing runtime input for field 'value: UnconstructableValue'"):
        NeedsUnconstructableField.with_injected()


def test_unsupported_annotation_raises_clear_error() -> None:
    with pytest.raises(TypeError, match="Cannot resolve field 'value'"):
        NeedsUnsupportedAnnotation.with_injected()


def test_factory_defaults_are_used_when_runtime_input_is_missing() -> None:
    db = Db()

    service = DefaultedFactory.with_injected(db=db)

    assert service.db is db
    assert service.label == "default"


def test_build_alias_matches_with_injected() -> None:
    db = Db()

    service = DefaultedFactory.build(db=db)

    assert service.db is db
    assert service.label == "default"


def test_variadic_factory_parameters_are_ignored() -> None:
    db = Db()

    service = VariadicFactory.with_injected(db=db)

    assert service.db is db


def test_unannotated_factory_parameter_requires_named_runtime_input() -> None:
    with pytest.raises(TypeError, match="Missing runtime input 'db' for from_env\\(\\)"):
        UnannotatedFactory.with_injected(database=Db())


def test_type_based_runtime_inputs_override_local_factory_defaults() -> None:
    service = NamedDefaultDoesNotOverrideTypedInput.with_injected(runtime_value="from-runtime")

    assert service.value == "from-runtime"


class MyBase:
    _settings: SomeSettings

    def __init__(self):
        self._settings = {"filled": "later"}

class SomeInjectable(Injectable):
    ...

class MyCls(MyBase, Injectable):
    inj: "SomeInjectable"

def test_dependencies_out_of_graph():

    inst = MyCls.with_injected()
    assert inst
    assert inst.inj
    assert inst._settings == {"filled": "later"}


