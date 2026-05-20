from __future__ import annotations

from logging import getLogger
from os import getenv
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

from .structs import HatchRustBuildConfig, HatchRustBuildPlan, wheel_tag
from .utils import import_string

__all__ = ("HatchRustBuildHook",)


class HatchRustBuildHook(BuildHookInterface[HatchRustBuildConfig]):
    """The hatch-rust build hook."""

    PLUGIN_NAME = "hatch-rs"
    _logger = getLogger(__name__)

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Initialize the plugin."""
        # Log some basic information
        project_name = self.metadata.config["project"]["name"]
        self._logger.info("Initializing hatch-rs plugin version %s", version)
        self._logger.info(f"Running hatch-rs: {project_name}")

        # Only run if creating wheel
        # TODO: Add support for specify sdist-plan
        if self.target_name != "wheel":
            self._logger.info("ignoring target name %s", self.target_name)
            return

        # Skip if SKIP_HATCH_RUST is set
        # TODO: Support CLI once https://github.com/pypa/hatch/pull/1743
        if getenv("SKIP_HATCH_RUST"):
            self._logger.info("Skipping the build hook since SKIP_HATCH_RUST was set")
            return

        # Get build config class or use default
        build_config_class = import_string(self.config["build-config-class"]) if "build-config-class" in self.config else HatchRustBuildConfig

        # Instantiate build config
        config = build_config_class(name=project_name, **self.config)

        # Get build plan class or use default
        build_plan_class = import_string(self.config["build-plan-class"]) if "build-plan-class" in self.config else HatchRustBuildPlan

        # Instantiate builder
        build_plan = build_plan_class(**config.model_dump())

        # Generate commands
        build_plan.generate()

        # Log commands if in verbose mode
        if config.verbose:
            for command in build_plan.commands:
                self._logger.warning(command)

        # Execute build plan
        try:
            build_plan.execute()
        finally:
            # Perform any cleanup actions
            build_plan.cleanup()

        if not build_plan.copied_artifacts and not build_plan.shared_data:
            raise ValueError("No libraries or generated outputs were created by the build.")

        # force include libraries
        # for library in build_plan._libraries:
        #     build_data["force_include"][library] = library
        #     build_data["pure_python"] = False
        #     machine = platform_machine()
        #     version_major = version_info.major
        #     version_minor = version_info.minor
        #     if "darwin" in sys_platform:
        #         os_name = "macosx_11_0"
        #     elif "linux" in sys_platform:
        #         os_name = "linux"
        #     else:
        #         os_name = "win"
        #     if all([lib.py_limited_api for lib in build_plan.libraries]):
        #         build_data["tag"] = f"cp{version_major}{version_minor}-abi3-{os_name}_{machine}"
        #     else:
        #         build_data["tag"] = f"cp{version_major}{version_minor}-cp{version_major}{version_minor}-{os_name}_{machine}"
        if build_plan.libraries:
            build_data["pure_python"] = False
            build_data["tag"] = wheel_tag(
                abi3=config.abi3,
                resolved_target=build_plan.resolved_target,
                platform_tag=config.wheel_platform_tag,
            )

        # force include libraries
        force_include = build_data.setdefault("force_include", {})
        for artifact in build_plan.copied_artifacts:
            force_include[artifact.distribution_path] = artifact.distribution_path

        shared_data = build_data.setdefault("shared_data", {})
        shared_data.update(build_plan.shared_data)

        for path in force_include:
            self._logger.warning(f"Force include: {path}")
        for source, destination in shared_data.items():
            self._logger.warning("Shared data: %s -> %s", source, destination)
