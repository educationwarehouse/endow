from __future__ import annotations

import abc

import pytest

from src.endow import Backend, Domain, Injectable, Service


class Db:
    def __init__(self) -> None:
        self.events: list[str] = []


class Counter(Injectable):
    value: int


class Applog(Service):
    db: Db

    @classmethod
    def from_env(cls, db: Db) -> Applog:
        instance = cls()
        instance.db = db
        return instance

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
        instance = cls()
        instance.db = db
        return instance

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(f"smtp:{recipient}:{subject}:{body}")


class FakeMailer(Mailer):
    @classmethod
    def from_env(cls, db: Db) -> FakeMailer:
        instance = cls()
        instance.db = db
        return instance

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


class AppBackend(Backend):
    products: Products
    methods: Methods


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
