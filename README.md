# endow

`endow` is a framework-agnostic, opinionated way to structure your backend.
It is a small dependency-injection runtime for Python 3.12+ applications. It wires objects from typed attributes, usually builds a shared graph from a backend root, and lets you pass runtime values such as `db` into the graph when it is created. When needed, you can also build an individual `Domain`, `Service`, or other `Injectable` directly.

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

## Building members inside a factory

Sometimes a factory only learns at runtime which classes it needs to construct - an environment variable naming one implementation, or several that get wrapped in a composite. Anything a `from_env` builds by itself is invisible to the graph, so it never gets wired.

Declare a `GraphBuilder` parameter to get a handle to the graph that is being built:

```python
import os
from endow import GraphBuilder

NOTIFIERS = {"mail": MailNotifier, "sms": SmsNotifier}


class ConfiguredNotifier(Notifier):
    @classmethod
    def from_env(cls, builder: GraphBuilder) -> Notifier:
        names = os.environ["NOTIFIERS"].split(",")
        if len(names) == 1:
            return builder.build(NOTIFIERS[names[0]])
        return CompositeNotifier(builder.build_all(NOTIFIERS[name] for name in names))
```

The parameter is matched by annotation, not by name, and it is optional: a `from_env` without one behaves exactly as before. It can be combined with the usual runtime inputs, `from_env(cls, db: DB, builder: GraphBuilder)`.

`builder.build(cls)` returns a fully wired instance, and `builder.build_all(classes)` does the same for several at once. What you get back:

- **Full wiring.** Each member is constructed through its own `from_env` if it has one, then gets its annotated fields filled - including dependencies the abstract base never declares. Members are complete before `build()` returns.
- **Shared singletons.** Members are cached in the same graph, so two calls for the same class give one object, and so does a sibling field annotated with that class.
- **Cycles.** Field cycles work as they always have. A factory asking the builder for a type whose own factory has not returned yet raises `Factory recursion detected` instead of a `RecursionError`. The same applies to a member with a field annotated with the contract its own entry point provides: that entry point is still being constructed, so there is no instance to hand over and the member is not allowed to satisfy the annotation with itself.

### Members are reachable by their own type, not by the contract

A field annotated with the abstract base resolves to whatever the graph resolved to directly - the entry point - not to one of its members:

```python
class App(BackendBase):
    notifier: ConfiguredNotifier
    contract: Notifier          # the composite, not one of its members
```

This holds for both shapes. With several names configured, `contract` is the composite. With one name configured, `ConfiguredNotifier.from_env` returns the member itself, so that member *is* what the graph resolved to and `contract` is that member.

Members stay reachable by their concrete type, so `mail: MailNotifier` elsewhere in the graph gets the same instance the composite holds. Naming a member that way does not promote it: `contract` still resolves to the entry point, not to the member that admin or debug code happens to hold.

When several instances satisfy a base annotation, one registered as the answer for a class it is not - the entry point, or any `from_env` returning a subclass - wins over one built for itself. Two of those is still ambiguous and raises.

One ordering caveat, inherited from how base annotations are satisfied in general: the field that pins the base to a concrete instance has to be resolved first. Declare the entry point above any field annotated with its base.

### Putting `from_env` on the abstract base instead

The dispatcher can live on the base rather than on a separate entry-point class, which is the shape `Mailer` uses in the test suite. Then the base becomes its own cache key and the ordering caveat above is the only thing to watch.

In that shape every member needs its own `from_env`, even a trivial one:

```python
class MailNotifier(Notifier):
    @classmethod
    def from_env(cls) -> "MailNotifier":
        return cls()
```

Without it, the member inherits the base's dispatcher, which asks the builder for the member again. That raises `Factory recursion detected`.

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
