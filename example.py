from __future__ import annotations

import abc
import os
from typing import Self

from pydal import DAL

from endow import Backend, Domain, Service


class Mailer(Service, abc.ABC):
    applog: Applog
    db: DAL

    @classmethod
    def from_env(cls, db: DAL) -> Self:
        if os.environ["SMTP_FAKE"] == "1":
            return FakeMailer.from_env(db=db)
        if os.environ.get("SMTP_API") == "1":
            return APIMailer.from_env(db=db)
        return SMTPMailer.from_env(db=db)

    @abc.abstractmethod
    def send(self, recipient: str, subject: str, body: str) -> None:
        raise NotImplementedError


class SMTPMailer(Mailer):
    @classmethod
    def from_env(cls, db: DAL) -> Self: ...

    def send(self, recipient: str, subject: str, body: str) -> None: ...


class FakeMailer(Mailer):
    @classmethod
    def from_env(cls, db: DAL) -> Self: ...

    def send(self, recipient: str, subject: str, body: str) -> None: ...


class APIMailer(Mailer):
    @classmethod
    def from_env(cls, db: DAL) -> Self: ...

    def send(self, recipient: str, subject: str, body: str) -> None: ...


class Applog(Service):
    @classmethod
    def from_env(cls, db: DAL) -> Self: ...

    def track(self, event: str, **context: object) -> None: ...


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


class AppBackend(Backend):
    products: Products
    methods: Methods


# Contract notes:
# - Typed attributes are the source of truth for dependency wiring.
# - Backend.from_env(...) builds one shared graph for this application root.
# - Nested from_env(...) hooks may ask for runtime inputs such as `db`.
# - Cycles like Products <-> Methods should work without wrapper types.

db = DAL(...)
backend = AppBackend.from_env(db=db)
backend.methods.update(method_id=42, product_id=7)
