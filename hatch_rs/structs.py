from __future__ import annotations

from os import chdir, curdir, environ, system as system_call
from pathlib import Path
from platform import machine as platform_machine
from shutil import which
from sys import platform as sys_platform
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

__all__ = (
    "HatchRustBuildConfig",
    "HatchRustBuildPlan",
)

BuildType = Literal["debug", "release"]
CompilerToolchain = Literal["gcc", "clang", "msvc"]
Language = Literal["c", "c++"]
Binding = Literal["cpython", "pybind11", "nanobind", "generic"]
Platform = Literal["linux", "darwin", "win32"]


class HatchRustBuildConfig(BaseModel):
    """Build config values for Hatch Rust Builder."""

    verbose: Optional[bool] = Field(default=False)
    name: Optional[str] = Field(default=None)

    module: str = Field(description="Python module name for the Rust extension.")
    path: Optional[Path] = Field(default=None, description="Path to the project root directory.")

    target: Optional[str] = Field(
        default=None,
        description="Target platform for the build. If not specified, it will be determined automatically.",
    )

    # Validate path
    @field_validator("path", mode="before")
    @classmethod
    def validate_path(cls, path: Optional[Path]) -> Path:
        if path is None:
            return Path.cwd()
        if not isinstance(path, Path):
            path = Path(path)
        if not path.is_dir():
            raise ValueError(f"Path '{path}' is not a valid directory.")
        return path


class HatchRustBuildPlan(HatchRustBuildConfig):
    build_type: BuildType = "release"
    commands: List[str] = Field(default_factory=list)

    def generate(self):
        self.commands = []

        # Construct build command
        platform = environ.get("HATCH_RUST_PLATFORM", sys_platform)
        machine = environ.get("HATCH_RUST_MACHINE", platform_machine())

        build_command = "cargo rustc"

        if self.build_type == "release":
            build_command += " --release"

        if not self.target:
            if platform == "win32":
                if machine in ("x86_64", "AMD64"):
                    self.target = "x86_64-pc-windows-msvc"
                elif machine == "i686":
                    self.target = "i686-pc-windows-msvc"
                elif machine in ("arm64", "aarch64"):
                    self.target = "aarch64-pc-windows-msvc"
                else:
                    raise ValueError(f"Unsupported machine type: {machine} for Windows platform")
            elif platform == "darwin":
                if machine == "x86_64":
                    self.target = "x86_64-apple-darwin"
                elif machine in ("arm64", "aarch64"):
                    self.target = "aarch64-apple-darwin"
                else:
                    raise ValueError(f"Unsupported machine type: {machine} for macOS platform")
            elif platform == "linux":
                if machine == "x86_64":
                    self.target = "x86_64-unknown-linux-gnu"
                elif machine == "i686":
                    self.target = "i686-unknown-linux-gnu"
                elif machine in ("arm64", "aarch64"):
                    self.target = "aarch64-unknown-linux-gnu"
                else:
                    raise ValueError(f"Unsupported machine type: {machine} for Linux platform")
            else:
                raise ValueError(f"Unsupported platform: {platform}")
        build_command += f" --target {self.target}"

        if "apple" in build_command:
            build_command += " -- -C link-arg=-undefined -C link-arg=dynamic_lookup"

        self.commands.append(build_command)

        # Add copy commands after build
        return self.commands

    def execute(self):
        """Execute the build commands."""
        # First navigate to the project path

        cwd = Path(curdir).resolve()
        chdir(self.path)

        for command in self.commands:
            system_call(command)

        # Go back to original path
        chdir(str(cwd))

        # After executing commands, grab the build artifacts in the target directory
        # and copy them to the current directory.
        target_path = Path(self.path) / "target" / self.target / self.build_type
        if not target_path.exists():
            raise FileNotFoundError(f"Target path '{target_path}' does not exist.")
        if not target_path.is_dir():
            raise NotADirectoryError(f"Target path '{target_path}' is not a directory.")

        platform = environ.get("HATCH_RUST_PLATFORM", sys_platform)
        if platform == "win32":
            files = list(target_path.glob("*.dll")) + list(target_path.glob("*.pyd"))
        elif platform == "linux":
            files = list(target_path.glob("*.so"))
        elif platform == "darwin":
            files = list(target_path.glob("*.dylib"))
        else:
            raise ValueError(f"Unsupported platform machine: {platform_machine()}")

        if not files:
            raise FileNotFoundError(f"No build artifacts found in '{target_path}'.")

        for file in files:
            if not file.is_file():
                continue

            # Convert the filename to module format
            file_name = file.stem.replace("lib", "", 1)  # Remove 'lib' prefix if present

            # Copy each file to the current directory
            if sys_platform == "win32":
                copy_command = f"copy {file} {cwd}\\{self.module}\\{file_name}.pyd"
            else:
                if which("cp") is None:
                    raise EnvironmentError("cp command not found. Ensure it is installed and available in PATH.")
                copy_command = f"cp -f {file} {cwd}/{self.module}/{file_name}.so"
                print(copy_command)
            system_call(copy_command)

        return self.commands

    def cleanup(self):
        ...
        # if self.platform.platform == "win32":
        #     for temp_obj in Path(".").glob("*.obj"):
        #         temp_obj.unlink()
