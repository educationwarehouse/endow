from __future__ import annotations

import typing as t

import pytest
from src.endow import BackendBase, Domain
from src.endow.policy import AuthenticationRequired, BasePolicy, PermissionDenied


class AuthContext:
    def __init__(
        self,
        allowed_permissions: set[str],
        *,
        authenticated: bool = True,
    ) -> None:
        self.allowed_permissions = allowed_permissions
        self.authenticated = authenticated

    def can(self, permission: str) -> bool:
        return permission in self.allowed_permissions


class PlatformPolicy(BasePolicy):
    auth: AuthContext

    def require_can_update(self) -> None:
        raise NotImplementedError()

    def require_can_publish(self) -> None:
        raise NotImplementedError()


class ProductPolicy(PlatformPolicy):
    auth: AuthContext

    def can_update(self):
        self.require_authenticated(self.auth.authenticated)
        if self.auth.can("products.update"):
            return self.allow()
        return self.deny("missing products.update permission")

    def require_can_update(self) -> None:
        self.require_allowed(self.can_update())

    def require_can_publish(self) -> None:
        self.require_authenticated(self.auth.authenticated)
        self.require_allowed(self.auth.can("products.publish"), "missing products.publish permission")


class FakeTable:
    @classmethod
    def set_dummy_data(cls, rows: list[dict[str, object]]):
        cls.rows = [row.copy() for row in rows]

    def update_matching(
        self,
        *,
        filters: dict[str, object],
        data: dict[str, object],
    ) -> dict[str, object]: ...


class FakeProductTable(FakeTable):
    @classmethod
    def update_matching(
        cls,
        filters: dict[str, object],
        data: dict[str, object],
    ) -> dict[str, object]:
        for row in cls.rows:
            if any(row.get(key) != value for key, value in filters.items()):
                continue

            row.update(data)
            return row.copy()

        msg = "No row matched the update"
        raise LookupError(msg)


class AuthorizedUpdate[TTable: FakeTable]:
    def __init__(
        self,
        *,
        table: TTable,
        filters: dict[str, object] | None = None,
    ) -> None:
        self.table = table
        self.filters = {} if filters is None else dict(filters)

    def where(self, **filters: object) -> AuthorizedUpdate[TTable]:
        return AuthorizedUpdate(
            table=self.table,
            filters={**self.filters, **filters},
        )

    def update(self, **data: object) -> dict[str, object]:
        return self.table.update_matching(filters=self.filters, data=data)


class AuthorizedPublish:
    def __init__(self, policy: PlatformPolicy) -> None:
        self.policy = policy

    def execute(self) -> str:
        self.policy.require_can_publish()
        return "published"


class ScopedRequestsMixin[TTable]:
    policy: PlatformPolicy

    @property
    def table(self):
        for base in self.__class__.__orig_bases__:
            if t.get_origin(base) is ScopedRequestsMixin:
                return t.get_args(base)[0]
        raise TypeError("TTable not found")

    def request_update(self) -> AuthorizedUpdate[TTable]:
        self.policy.require_can_update()
        return AuthorizedUpdate(table=self.table)
        # fixme: think about an alternative like
        # AuthorizedUpdate(table=self.table, requirement=self.policy.require_can_update)
        # so you can't forget to call policy.require_can_update() ?

    def request_publish(self) -> AuthorizedPublish:
        # fixme: not a great example
        return AuthorizedPublish(self.policy)


class Products(Domain, ScopedRequestsMixin[FakeProductTable]):
    policy: ProductPolicy
    # self.table doesn't exist, uses argument on ScopedRequestsMixin

    def update(self, product_id: int, name: str) -> dict[str, object]:
        return self.request_update().where(id=product_id).update(name=name)

    def publish(self) -> str:
        return self.request_publish().execute()


class AppBackend(BackendBase):
    products: Products


def test_policy_builds_with_injected_runtime_inputs() -> None:
    auth = AuthContext({"products.update"})

    policy = ProductPolicy.with_injected(auth=auth)

    assert policy.auth is auth


def test_domain_update_flows_through_request_update_and_wrapper() -> None:
    FakeProductTable.set_dummy_data(
        [
            {"id": 1, "name": "old"},
            {"id": 2, "name": "other"},
        ],
    )
    backend = AppBackend.with_injected(
        auth=AuthContext({"products.update"}),
    )

    updated = backend.products.update(product_id=1, name="new")

    assert updated == {"id": 1, "name": "new"}


def test_request_update_rejects_missing_permission_before_returning_wrapper() -> None:
    FakeProductTable.set_dummy_data([{"id": 1, "name": "old"}])
    backend = AppBackend.with_injected(
        auth=AuthContext(set()),
    )

    with pytest.raises(PermissionDenied, match="missing products.update permission"):
        backend.products.update(product_id=1, name="new")


def test_request_update_rejects_unauthenticated_actor() -> None:
    FakeProductTable.set_dummy_data([{"id": 1, "name": "old"}])
    backend = AppBackend.with_injected(
        auth=AuthContext(
            {"products.update"},
            authenticated=False,
        ),
    )

    with pytest.raises(AuthenticationRequired, match="Authentication required"):
        backend.products.update(product_id=1, name="new")


def test_domain_publish_flows_through_boolean_require_allowed_branch() -> None:
    backend = AppBackend.with_injected(
        auth=AuthContext({"products.publish"}),
    )

    assert backend.products.publish() == "published"


def test_publish_rejects_missing_permission_through_boolean_require_allowed_branch() -> None:
    backend = AppBackend.with_injected(
        auth=AuthContext(set()),
    )

    with pytest.raises(PermissionDenied, match="missing products.publish permission"):
        backend.products.publish()
