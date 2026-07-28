from hatchling.plugin import hookimpl

from .plugin import HatchRustBuildHook


@hookimpl
def hatch_register_build_hook() -> type[HatchRustBuildHook]:
    return HatchRustBuildHook
