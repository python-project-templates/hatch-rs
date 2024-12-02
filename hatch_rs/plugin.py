from __future__ import annotations

import typing as t
from dataclasses import dataclass, field

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


@dataclass
class HatchRustBuildConfig(BuilderConfig):
    """Build config values for Hatch Rust Builder."""

    toolchain: str | None = None
    build_kwargs: t.Mapping[str, str] = field(default_factory=dict)
    editable_build_kwargs: t.Mapping[str, str] = field(default_factory=dict)
    ensured_targets: list[str] = field(default_factory=list)
    skip_if_exists: list[str] = field(default_factory=list)
    optional_editable_build: str = ""


class HatchRustBuildHook(BuildHookInterface[HatchRustBuildConfig]):
    """The hatch-rust build hook."""

    PLUGIN_NAME = "hatch-rust"

    def initialize(self, version: str, build_data: dict[str, t.Any]) -> None:
        """Initialize the plugin."""
        return
