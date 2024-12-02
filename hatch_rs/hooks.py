from typing import Type

from hatchling.plugin import hookimpl

from .plugin import HatchRustBuildHook


@hookimpl
def hatch_register_build_hook() -> Type[HatchRustBuildHook]:
    return HatchRustBuildHook
