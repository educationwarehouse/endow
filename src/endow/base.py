class Injectable:
    """Base class for objects that participate in endow graph wiring."""


class Service(Injectable):
    """Infrastructure capability resolved by the runtime."""


class Domain(Injectable):
    """Domain component resolved by the runtime."""
