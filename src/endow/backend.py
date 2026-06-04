from __future__ import annotations

import typing as t

from .base import Injectable
from .runtime import build_graph


class BackendBase(Injectable):
    @classmethod
    def from_env(cls, **runtime_inputs: t.Any) -> t.Self:
        return build_graph(cls, runtime_inputs)
