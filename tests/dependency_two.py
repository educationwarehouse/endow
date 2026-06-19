import typing as t

from src.endow import Injectable

if t.TYPE_CHECKING:
    from .dependency_one import FirstDependency

class SecondDependency(Injectable):
    first: FirstDependency
