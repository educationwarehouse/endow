# endow

`endow` is a small dependency-injection runtime for Python 3.12+ applications.
It wires objects from typed attributes, usually builds a shared graph from a backend root, and lets you pass runtime values such as `db` into the graph when it is created. When needed, you can also build an individual `Domain`, `Service`, or other `Injectable` directly.

## Install

```bash
uv pip install endow
```

## Example

```python
from endow import BackendBase, Domain, Service


class DB:
    def __init__(self, dsn: str) -> None:
        ...

class Applog(Service):
    db: DB

    def track(self, event: str, **context: object) -> None:
        print(event, context)


class Mailer(Service):
    applog: Applog
    db: DB

    @classmethod
    def from_env(cls, db: DB) -> "Mailer":
        return cls()

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.applog.track(
            "mail.sent",
            recipient=recipient,
            subject=subject,
            body=body,
        )


class Products(Domain):
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


class AppBackend(BackendBase):
    products: Products


db = DB("postgresql://user:pass@localhost/app")
backend = AppBackend.with_injected(db=db)
backend.products.update(product_id=7)
```

## How it works

- Typed attributes are the source of truth for wiring.
- `with_injected(...)` usually builds one shared object graph from a backend root, but it can also build a standalone `Domain`, `Service`, or other `Injectable`.
- `Service` and `Domain` are lightweight markers that participate in the graph.
- Nested `from_env(...)` hooks can receive runtime inputs from the root call.
- Cycles in the graph are supported because instances are cached during construction.

## Service vs Domain

Use the two markers to communicate architectural intent:

- `Service` is for infrastructure and external-facing capabilities, such as logging, mail, persistence, or API clients.
- `Domain` is for business logic and application workflows that coordinate those capabilities.

The dependency direction should stay one way:

- `Domain` objects may depend on `Service` objects.
- `Service` objects should not depend on `Domain` objects.

That keeps the graph aligned with layered architecture and one-way data flow: the business layer can use infrastructure, but infrastructure should not reach back into business logic.

By default, that rule is a convention rather than an enforced runtime check. If you want the graph to enforce it, use `BackendBase.with_injected_checked(strict, ...)`:

- `strict=True` turns `Service`-to-`Domain` dependencies into errors.
- `strict=False` leaves the dependency in place but emits a warning.

Use `with_injected(...)` when you want the current permissive behavior without checking.

## Why not make everything a Service

If everything is a `Service`, the graph stops expressing the difference between business behavior and infrastructure concerns. Keeping `Domain` separate makes the direction of dependencies visible, which helps prevent business rules from leaking into adapters and makes the architecture easier to read and review.

## Policies and authorization

`endow.policy` helps keep authorization logic out of domain methods.

- Use `BasePolicy.require_authenticated(...)` for auth checks.
- Use `BasePolicy.require_allowed(...)` for simple yes/no permission checks.
- Use `AuthorizationResult`, `Allow`, and `Deny` when a policy should return something richer than a boolean.

```python
from endow import Domain
from endow.policy import Allow, AuthorizationResult, BasePolicy, Deny


class AuthContext:  # this is a minimal example
    def can(self, permission: str) -> bool:
        ...


class ProductPolicy(BasePolicy):
    auth: AuthContext

    def request_update(self) -> AuthorizationResult:
        if not self.auth.can("products.update"):
            return Deny("missing products.update permission")

        return Allow(
            apply=lambda query: query.where(
                lambda row: row.owner_id == self.auth.user_id,
            ),
        )

    def require_update(self) -> Allow:
        return self.request_update().require()


class ScopedDomain(Domain):
    policy: ProductPolicy
    table: ...  # ORM-specific query/table object

    def require_update(self):
        authz = self.policy.require_update()
        query = self.table.permissions(update=True)
        return authz(query)


class Products(ScopedDomain):
    def update_name(self, new_name: str):
        return self.require_update().update(name=new_name)
```

The common pattern is:

- the shared domain helper sets up the base operation, such as `.permissions(update=True)`
- the policy either denies the request or returns an `Allow(...)` result that applies extra query shaping

That shaping can use `.where(...)`, `.select(...)`, joins, or other ORM-specific operations when needed.
