"""Backend entry points for constructing dependency graphs."""

from __future__ import annotations

import typing as t

from .base import Injectable
from .runtime import build_graph


class BackendBase(Injectable):
    """Root object that builds a graph from runtime inputs."""

    @classmethod
    def from_env(cls, **runtime_inputs: t.Any) -> t.Self:
        """Build a backend instance from the provided runtime inputs."""
        return build_graph(cls, runtime_inputs)

    @classmethod
    def from_env_checked(cls, strict: bool, **runtime_inputs: t.Any) -> t.Self:
        """Build a backend instance and check Service-to-Domain dependencies."""
        return build_graph(cls, runtime_inputs, strict=strict)
