"""Tests for factories that build other injectables inside the graph under construction."""

from __future__ import annotations

import abc

import pytest
from src.endow import BackendBase, Domain, GraphBuilder, Injectable, Service


class Db:
    def __init__(self) -> None:
        self.events: list[str] = []


class Applog(Service):
    db: Db

    def track(self, event: str) -> None:
        self.db.events.append(event)


class Metrics(Service):
    applog: Applog

    def count(self, name: str) -> None:
        self.applog.track(f"metric:{name}")


class Notifier(Service, abc.ABC):
    applog: Applog

    @abc.abstractmethod
    def notify(self, message: str) -> None:
        raise NotImplementedError


class MailNotifier(Notifier):
    db: Db

    def notify(self, message: str) -> None:
        self.applog.track(f"mail:{message}")


class SmsNotifier(Notifier):
    metrics: Metrics

    def notify(self, message: str) -> None:
        self.metrics.count("sms")
        self.applog.track(f"sms:{message}")


NOTIFIERS: dict[str, type[Notifier]] = {"mail": MailNotifier, "sms": SmsNotifier}


class CompositeNotifier(Notifier):
    def __init__(self, members: list[Notifier]) -> None:
        self.members = members

    def notify(self, message: str) -> None:
        for member in self.members:
            member.notify(message)


class ConfiguredNotifier(Notifier):
    """Entry point: picks one implementation or a composite, based on config."""

    @classmethod
    def from_env(cls, builder: GraphBuilder) -> Notifier:
        names = str(builder.inputs["notifiers"]).split(",")
        if len(names) == 1:
            return builder.build(NOTIFIERS[names[0]])
        return CompositeNotifier(builder.build_all(NOTIFIERS[name] for name in names))


class App(BackendBase):
    notifier: ConfiguredNotifier
    applog: Applog
    metrics: Metrics


def test_single_implementation_is_fully_wired() -> None:
    db = Db()
    app = App.with_injected(db=db, notifiers="sms")

    assert isinstance(app.notifier, SmsNotifier)
    app.notifier.notify("hi")
    assert db.events == ["metric:sms", "sms:hi"]

    assert app.notifier.applog is app.applog
    assert app.notifier.metrics is app.metrics


def test_composite_members_are_wired_and_share_singletons() -> None:
    db = Db()
    app = App.with_injected(db=db, notifiers="mail,sms")

    composite = app.notifier
    assert isinstance(composite, CompositeNotifier)
    mail, sms = composite.members
    assert isinstance(mail, MailNotifier)
    assert isinstance(sms, SmsNotifier)

    # members got dependencies the abstract contract never declared
    assert mail.db is db
    assert sms.metrics is app.metrics

    # and they share the graph's singletons
    assert mail.applog is app.applog
    assert sms.applog is app.applog
    assert sms.metrics.applog is app.applog

    composite.notify("hi")
    assert db.events == ["mail:hi", "metric:sms", "sms:hi"]


def test_builder_instances_are_cached_in_the_graph() -> None:
    class Shared(Service):
        db: Db

    class Twice(Service):
        @classmethod
        def from_env(cls, builder: GraphBuilder) -> Twice:
            instance = cls()
            instance.first = builder.build(Shared)
            instance.second = builder.build(Shared)
            return instance

    class Root(BackendBase):
        twice: Twice
        shared: Shared

    root = Root.with_injected(db=Db())
    assert root.twice.first is root.twice.second
    assert root.twice.first is root.shared


def test_builder_can_be_combined_with_runtime_inputs() -> None:
    class Picky(Service):
        @classmethod
        def from_env(cls, db: Db, builder: GraphBuilder) -> Picky:
            instance = cls()
            instance.db = db
            instance.applog = builder.build(Applog)
            return instance

    db = Db()
    picky = Picky.with_injected(db=db)
    assert picky.db is db
    picky.applog.track("ok")
    assert db.events == ["ok"]


def test_builder_is_not_offered_as_a_field_dependency() -> None:
    class Consumer(Service):
        db: Db

        @classmethod
        def from_env(cls, builder: GraphBuilder) -> Consumer:
            return cls()

    db = Db()
    consumer = Consumer.with_injected(db=db)
    assert consumer.db is db


def test_factory_recursion_raises() -> None:
    class Recursive(Service):
        @classmethod
        def from_env(cls, builder: GraphBuilder) -> Recursive:
            return builder.build(Recursive)

    with pytest.raises(TypeError, match="Factory recursion detected"):
        Recursive.with_injected()


def test_indirect_factory_recursion_raises() -> None:
    class Left(Service):
        @classmethod
        def from_env(cls, builder: GraphBuilder) -> Left:
            builder.build(Right)
            return cls()

    class Right(Service):
        @classmethod
        def from_env(cls, builder: GraphBuilder) -> Right:
            builder.build(Left)
            return cls()

    with pytest.raises(TypeError, match="Factory recursion detected"):
        Left.with_injected()


def test_field_cycles_still_work_around_a_builder_factory() -> None:
    class Cyclic(Domain):
        other: Other

        @classmethod
        def from_env(cls, builder: GraphBuilder) -> Cyclic:
            instance = cls()
            instance.applog = builder.build(Applog)
            return instance

    class Other(Domain):
        cyclic: Cyclic

    db = Db()
    cyclic = Cyclic.with_injected(db=db)
    assert cyclic.other.cyclic is cyclic
    cyclic.applog.track("ok")
    assert db.events == ["ok"]


def test_builder_repr_names_what_is_under_construction() -> None:
    seen: list[str] = []

    class Reporting(Injectable):
        @classmethod
        def from_env(cls, builder: GraphBuilder) -> Reporting:
            seen.append(repr(builder))
            return cls()

    Reporting.with_injected()
    assert seen == ["<GraphBuilder building=Reporting>"]
