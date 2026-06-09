from __future__ import annotations

import abc
import os
import typing as t

from pydal import DAL

from endow import BackendBase, Domain, Service


class Mailer(Service, abc.ABC):
    applog: Applog
    db: DAL

    @classmethod
    def from_env(cls, db: DAL) -> t.Self:
        if os.environ.get("SMTP_FAKE") == "1":
            return FakeMailer.from_env(db)
        elif os.environ.get("SMTP_API") == "1":
            return APIMailer.from_env(db)
        else:
            return SMTPMailer.from_env(db)

    @abc.abstractmethod
    def send(self, recipient: str, subject: str, body: str) -> None:
        raise NotImplementedError()


class SMTPMailer(Mailer):
    def __init__(self, smtp_credentials: dict[str, str]) -> None:
        super().__init__()
        self.smtp_credentials = smtp_credentials

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(
            "mailer.smtp.sent",
            recipient=recipient,
            subject=subject,
            body=body,
        )

    @classmethod
    def from_env(cls, db: DAL) -> t.Self:
        return cls(smtp_credentials=dict(os.environ))


class FakeMailer(Mailer):
    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(
            "mailer.fake.sent",
            recipient=recipient,
            subject=subject,
            body=body,
        )

    @classmethod
    def from_env(cls, db: DAL):
        return cls()


class APIMailer(Mailer):
    @classmethod
    def from_env(cls, _: DAL) -> t.Self:
        return cls()

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(
            "mailer.api.sent",
            recipient=recipient,
            subject=subject,
            body=body,
        )


class Applog(Service):
    db: DAL

    @classmethod
    def from_env(cls, _db: DAL) -> t.Self:
        return cls()

    def track(self, event: str, **context: t.Any) -> None:
        print(event, context)


class Products(Domain):
    methods: Methods
    mailer: Mailer
    applog: Applog

    def update(self, product_id: int) -> None:
        self.applog.track("products.update.started", product_id=product_id)
        self.mailer.send(
            recipient="ops@example.com",
            subject="Product updated",
            body=f"product_id={product_id}",
        )
        self.applog.track("products.update.finished", product_id=product_id)


class Methods(Domain):
    products: Products
    applog: Applog

    def update(self, method_id: int, product_id: int) -> None:
        self.applog.track(
            "methods.update.started",
            method_id=method_id,
            product_id=product_id,
        )
        self.products.update(product_id=product_id)
        self.applog.track(
            "methods.update.finished",
            method_id=method_id,
            product_id=product_id,
        )


class Backend(BackendBase):
    products: Products
    methods: Methods


# Contract notes:
# - Typed attributes are the source of truth for dependency wiring.
# - with_injected(...) builds one shared graph for the requested root.
# - Nested from_env(...) hooks may ask for runtime inputs such as `db`.
# - Cycles like Products <-> Methods should work without wrapper types.

db = DAL("sqlite:memory:")
backend = Backend.with_injected_checked(False, db=db)
backend.methods.update(method_id=42, product_id=7)
