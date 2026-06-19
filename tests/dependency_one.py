import typing as t

from src.endow import Injectable

if t.TYPE_CHECKING:
    from .dependency_two import SecondDependency

class FirstDependency(Injectable):
    second: SecondDependency