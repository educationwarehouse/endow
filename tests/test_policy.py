from __future__ import annotations

import typing as t

import pytest
from src.endow import BackendBase, Domain
from src.endow.policy import (
    Allow,
    AuthenticationRequired,
    AuthorizationResult,
    BasePolicy,
    Deny,
    PermissionDenied,
)


class AuthContext:
    def __init__(
        self,
        allowed_permissions: set[str],
        *,
        allowed_gids: set[str] | None = None,
        authenticated: bool = True,
    ) -> None:
        self.allowed_permissions = allowed_permissions
        self.allowed_gids = allowed_gids if allowed_gids is not None else set()
        self.authenticated = authenticated

    def can(self, permission: str) -> bool:
        return permission in self.allowed_permissions


class PlatformPolicy(BasePolicy):
    auth: AuthContext

    def request_update(self) -> AuthorizationResult[t.Any]:
        raise NotImplementedError()

    def require_update(self) -> Allow[t.Any]:
        return self.request_update().require()

    def require_can_publish(self) -> None:
        raise NotImplementedError()


class ProductPolicy(PlatformPolicy):
    auth: AuthContext

    def request_update(self) -> AuthorizationResult[t.Any]:
        self.require_authenticated(self.auth.authenticated)
        if not self.auth.can("products.update"):
            return Deny("missing products.update permission")

        return Allow(
            apply=lambda query: query.select("id", "gid", "name").where(
                lambda row: row.get("gid") in self.auth.allowed_gids,
            ),
        )

    def require_can_publish(self) -> None:
        self.require_authenticated(self.auth.authenticated)
        self.require_allowed(self.auth.can("products.publish"), "missing products.publish permission")


class FakeTable:
    @classmethod
    def set_dummy_data(cls, rows: list[dict[str, object]]):
        cls.rows = [row.copy() for row in rows]

    @classmethod
    def permissions(cls, *, update: bool = False) -> AuthorizedUpdate[t.Self]:
        if not update:
            msg = "Only update permissions are supported in the example"
            raise ValueError(msg)
        return AuthorizedUpdate(table=cls)

    def update_matching(
        self,
        *,
        conditions: tuple[t.Callable[[dict[str, object]], bool], ...],
        filters: dict[str, object],
        data: dict[str, object],
    ) -> dict[str, object]: ...


class FakeProductTable(FakeTable):
    @classmethod
    def update_matching(
        cls,
        conditions: tuple[t.Callable[[dict[str, object]], bool], ...],
        filters: dict[str, object],
        data: dict[str, object],
    ) -> dict[str, object]:
        for row in cls.rows:
            if any(not condition(row) for condition in conditions):
                continue
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
        conditions: tuple[t.Callable[[dict[str, object]], bool], ...] = (),
        filters: dict[str, object] | None = None,
        selected_columns: tuple[str, ...] | None = None,
    ) -> None:
        self.table = table
        self.conditions = conditions
        self.filters = {} if filters is None else dict(filters)
        self.selected_columns = selected_columns

    def where(
        self,
        *conditions: t.Callable[[dict[str, object]], bool],
        **filters: object,
    ) -> AuthorizedUpdate[TTable]:
        return AuthorizedUpdate(
            table=self.table,
            conditions=self.conditions + conditions,
            filters={**self.filters, **filters},
            selected_columns=self.selected_columns,
        )

    def select(self, *columns: str) -> AuthorizedUpdate[TTable]:
        return AuthorizedUpdate(
            table=self.table,
            conditions=self.conditions,
            filters=self.filters,
            selected_columns=columns if columns else self.selected_columns,
        )

    def update(self, **data: object) -> dict[str, object]:
        return self.table.update_matching(
            conditions=self.conditions,
            filters=self.filters,
            data=data,
        )


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
        authz = self.policy.require_update()
        query = self.table.permissions(update=True)
        return authz(query)

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
    auth = AuthContext({"products.update"}, allowed_gids={"team-a"})

    policy = ProductPolicy.with_injected(auth=auth)

    assert policy.auth is auth


def test_policy_query_transform_is_applied_before_domain_filters() -> None:
    FakeProductTable.set_dummy_data(
        [
            {"id": 1, "gid": "team-a", "name": "old"},
            {"id": 2, "gid": "team-b", "name": "other"},
        ],
    )
    backend = AppBackend.with_injected(
        auth=AuthContext({"products.update"}, allowed_gids={"team-a"}),
    )

    query = backend.products.request_update()

    assert query.selected_columns == ("id", "gid", "name")


def test_domain_update_flows_through_request_update_and_wrapper() -> None:
    FakeProductTable.set_dummy_data(
        [
            {"id": 1, "gid": "team-a", "name": "old"},
            {"id": 2, "gid": "team-b", "name": "other"},
        ],
    )
    backend = AppBackend.with_injected(
        auth=AuthContext({"products.update"}, allowed_gids={"team-a"}),
    )

    updated = backend.products.update(product_id=1, name="new")

    assert updated == {"id": 1, "gid": "team-a", "name": "new"}


def test_request_update_hides_rows_outside_policy_scope() -> None:
    FakeProductTable.set_dummy_data(
        [
            {"id": 1, "gid": "team-a", "name": "old"},
            {"id": 2, "gid": "team-b", "name": "other"},
        ],
    )
    backend = AppBackend.with_injected(
        auth=AuthContext({"products.update"}, allowed_gids={"team-a"}),
    )

    with pytest.raises(LookupError, match="No row matched the update"):
        backend.products.update(product_id=2, name="new")


def test_request_update_rejects_missing_permission_before_returning_wrapper() -> None:
    FakeProductTable.set_dummy_data([{"id": 1, "gid": "team-a", "name": "old"}])
    backend = AppBackend.with_injected(
        auth=AuthContext(set(), allowed_gids={"team-a"}),
    )

    with pytest.raises(PermissionDenied, match="missing products.update permission"):
        backend.products.update(product_id=1, name="new")


def test_request_update_rejects_unauthenticated_actor() -> None:
    FakeProductTable.set_dummy_data([{"id": 1, "gid": "team-a", "name": "old"}])
    backend = AppBackend.with_injected(
        auth=AuthContext(
            {"products.update"},
            allowed_gids={"team-a"},
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
