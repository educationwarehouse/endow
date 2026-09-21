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
    def from_env(cls, notifiers: str, builder: GraphBuilder) -> Notifier:
        names = notifiers.split(",")
        if len(names) == 1:
            return builder.build(NOTIFIERS[names[0]])
        return CompositeNotifier(builder.build_all(NOTIFIERS[name] for name in names))


class App(BackendBase):
    notifier: ConfiguredNotifier
    contract: Notifier
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


def test_entry_point_satisfies_the_base_annotation_for_a_composite() -> None:
    app = App.with_injected(db=Db(), notifiers="mail,sms")

    # the composite is what the graph resolved to, so it stands in for Notifier;
    # its members do not, even though they subclass Notifier too
    assert app.contract is app.notifier
    assert isinstance(app.contract, CompositeNotifier)


def test_entry_point_satisfies_the_base_annotation_for_a_single_member() -> None:
    app = App.with_injected(db=Db(), notifiers="sms")

    # here the member *is* what ConfiguredNotifier resolved to, so it is promoted
    assert app.contract is app.notifier
    assert isinstance(app.contract, SmsNotifier)


class Reports(Service):
    mail: MailNotifier


class RootWithReports(BackendBase):
    notifier: ConfiguredNotifier
    reports: Reports
    contract: Notifier


class RootWithReportsFirst(BackendBase):
    reports: Reports
    notifier: ConfiguredNotifier
    contract: Notifier


def test_members_stay_reachable_by_their_own_type() -> None:
    root = RootWithReports.with_injected(db=Db(), notifiers="mail,sms")
    assert root.reports.mail is root.notifier.members[0]


@pytest.mark.parametrize("root_cls", [RootWithReports, RootWithReportsFirst])
def test_a_directly_referenced_member_does_not_shadow_the_entry_point(root_cls: type) -> None:
    # asking for MailNotifier by name must not make it a candidate for Notifier,
    # whichever side of the entry-point field the reference sits on
    root = root_cls.with_injected(db=Db(), notifiers="mail,sms")

    assert isinstance(root.contract, CompositeNotifier)
    assert root.contract is root.notifier
    assert root.reports.mail is root.notifier.members[0]


class SingleRootWithReports(BackendBase):
    notifier: ConfiguredNotifier
    reports: Reports
    contract: Notifier


def test_single_member_entry_point_survives_a_direct_reference() -> None:
    # ConfiguredNotifier resolves to the SmsNotifier itself, so that member stands in
    # for Notifier even though MailNotifier is also in the graph by name
    root = SingleRootWithReports.with_injected(db=Db(), notifiers="sms")

    assert isinstance(root.contract, SmsNotifier)
    assert root.contract is root.notifier
    assert isinstance(root.reports.mail, MailNotifier)


class Shared(Service):
    db: Db


class Twice(Service):
    @classmethod
    def from_env(cls, builder: GraphBuilder) -> Twice:
        instance = cls()
        instance.first = builder.build(Shared)
        instance.second = builder.build(Shared)
        return instance


class TwiceRoot(BackendBase):
    twice: Twice
    shared: Shared


def test_builder_instances_are_cached_in_the_graph() -> None:
    root = TwiceRoot.with_injected(db=Db())
    assert root.twice.first is root.twice.second
    assert root.twice.first is root.shared


class Picky(Service):
    @classmethod
    def from_env(cls, db: Db, builder: GraphBuilder) -> Picky:
        instance = cls()
        instance.db = db
        instance.applog = builder.build(Applog)
        return instance


def test_builder_can_be_combined_with_runtime_inputs() -> None:
    db = Db()
    picky = Picky.with_injected(db=db)
    assert picky.db is db
    picky.applog.track("ok")
    assert db.events == ["ok"]


class Consumer(Service):
    db: Db

    @classmethod
    def from_env(cls, builder: GraphBuilder) -> Consumer:
        return cls()


def test_builder_is_not_offered_as_a_field_dependency() -> None:
    db = Db()
    consumer = Consumer.with_injected(db=db)
    assert consumer.db is db


class Recursive(Service):
    @classmethod
    def from_env(cls, builder: GraphBuilder) -> Recursive:
        return builder.build(Recursive)


def test_factory_recursion_raises() -> None:
    with pytest.raises(TypeError, match="Factory recursion detected"):
        Recursive.with_injected()


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


def test_indirect_factory_recursion_raises() -> None:
    with pytest.raises(TypeError, match="Factory recursion detected"):
        Left.with_injected()


class Cyclic(Domain):
    other: Other

    @classmethod
    def from_env(cls, builder: GraphBuilder) -> Cyclic:
        instance = cls()
        instance.applog = builder.build(Applog)
        return instance


class Other(Domain):
    cyclic: Cyclic


def test_field_cycles_still_work_around_a_builder_factory() -> None:
    db = Db()
    cyclic = Cyclic.with_injected(db=db)
    assert cyclic.other.cyclic is cyclic
    cyclic.applog.track("ok")
    assert db.events == ["ok"]


class Reporting(Injectable):
    seen: list[str] = []

    @classmethod
    def from_env(cls, builder: GraphBuilder) -> Reporting:
        cls.seen.append(repr(builder))
        return cls()


def test_builder_repr_names_what_is_under_construction() -> None:
    Reporting.seen = []
    Reporting.with_injected()
    assert Reporting.seen == ["<GraphBuilder building=Reporting>"]


class Retrying(Notifier):
    """A member that wants the contract back, to re-dispatch through it."""

    fallback: Notifier

    def notify(self, message: str) -> None:
        self.fallback.notify(message)


RETRY_NOTIFIERS: dict[str, type[Notifier]] = {"mail": MailNotifier, "retry": Retrying}


class RetryEntry(Notifier):
    @classmethod
    def from_env(cls, notifiers: str, builder: GraphBuilder) -> Notifier:
        return CompositeNotifier(builder.build_all(RETRY_NOTIFIERS[name] for name in notifiers.split(",")))

    def notify(self, message: str) -> None:
        raise NotImplementedError


class RetryRoot(BackendBase):
    notifier: RetryEntry


def test_member_cannot_satisfy_its_own_contract_while_the_entry_point_is_building() -> None:
    # the composite does not exist yet, so there is no instance to hand the member;
    # it must not quietly wire itself as its own fallback
    with pytest.raises(TypeError, match="provided by 'RetryEntry', which is under construction"):
        RetryRoot.with_injected(db=Db(), notifiers="mail,retry")


class SelfBuilding(Service):
    @classmethod
    def from_env(cls, builder: GraphBuilder) -> SelfBuilding:
        return builder.build(SelfBuilding)


def test_a_factory_cannot_build_itself_through_the_builder() -> None:
    with pytest.raises(TypeError, match="Factory recursion detected"):
        SelfBuilding.with_injected()


class SlackNotifier(Notifier):
    def notify(self, message: str) -> None:
        self.applog.track(f"slack:{message}")


class RetryingWithChannel(Notifier):
    """A member holding a concrete notifier as well as the contract."""

    slack: SlackNotifier
    fallback: Notifier

    def notify(self, message: str) -> None:
        self.fallback.notify(message)


class ContractFirst(Notifier):
    """Same fields, declared the other way around."""

    fallback: Notifier
    slack: SlackNotifier

    def notify(self, message: str) -> None:
        self.fallback.notify(message)


class ChannelEntry(Notifier):
    @classmethod
    def from_env(cls, member: str, builder: GraphBuilder) -> Notifier:
        return builder.build(RetryingWithChannel if member == "concrete-first" else ContractFirst)

    def notify(self, message: str) -> None:
        raise NotImplementedError


class ChannelRoot(BackendBase):
    notifier: ChannelEntry


@pytest.mark.parametrize("member", ["concrete-first", "contract-first"])
def test_a_sibling_field_cannot_satisfy_a_member_contract(member: str) -> None:
    # a concrete notifier built for an adjacent field must not be handed to the
    # contract field, whichever order the two are declared in
    with pytest.raises(TypeError, match="provided by 'ChannelEntry', which is under construction"):
        ChannelRoot.with_injected(db=Db(), member=member)
