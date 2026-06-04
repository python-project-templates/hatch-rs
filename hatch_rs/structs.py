from __future__ import annotations

from ctypes import CDLL
from dataclasses import dataclass
from glob import glob
from json import dumps
from os import environ
from pathlib import Path
from platform import machine as platform_machine
from re import sub
from shlex import join as shell_join
from shutil import copy2
from subprocess import run
from sys import platform as sys_platform, version_info
from tempfile import TemporaryDirectory
from typing import Any, List, Literal, Optional

from packaging.tags import cpython_tags, mac_platforms
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

__all__ = (
    "CargoInvocation",
    "CopiedArtifact",
    "GeneratedOutputConfig",
    "HatchRustBuildConfig",
    "HatchRustBuildPlan",
    "ResolvedTarget",
    "RustArtifactConfig",
    "ValidationCommandConfig",
    "python_extension_name",
    "resolve_target_triple",
    "shared_library_name",
    "wheel_tag",
)

BuildType = Literal["debug", "release"]
CargoTargetKind = Literal["lib", "bin", "example", "test", "bench"]
CbindgenMode = Literal["cli", "build-script"]
CompilerToolchain = Literal["gcc", "clang", "msvc"]
InstallScheme = Literal["package", "shared-data", "validate-only"]
Language = Literal["c", "c++"]
Binding = Literal["cpython", "pybind11", "nanobind", "generic"]
Platform = Literal["linux", "darwin", "win32"]


@dataclass(frozen=True)
class ResolvedTarget:
    """Resolved host platform, machine, and Rust target triple."""

    platform: str
    machine: str
    triple: str


@dataclass(frozen=True)
class CargoInvocation:
    """A Cargo command to execute in a specific working directory."""

    args: tuple[str, ...]
    cwd: Path
    env: dict[str, str]

    @property
    def display(self) -> str:
        """Return a shell-style string for logging and error messages."""
        return shell_join(self.args)


@dataclass(frozen=True)
class CopiedArtifact:
    """A build artifact copied into the wheel source tree."""

    source: Path
    destination: Path
    distribution_path: str


WINDOWS_TARGETS = {
    "x86_64": "x86_64-pc-windows-msvc",
    "i686": "i686-pc-windows-msvc",
    "aarch64": "aarch64-pc-windows-msvc",
}

DARWIN_TARGETS = {
    "x86_64": "x86_64-apple-darwin",
    "aarch64": "aarch64-apple-darwin",
}

LINUX_GNU_TARGETS = {
    "x86_64": "x86_64-unknown-linux-gnu",
    "i686": "i686-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
    "armv7": "armv7-unknown-linux-gnueabihf",
    "ppc64le": "powerpc64le-unknown-linux-gnu",
    "s390x": "s390x-unknown-linux-gnu",
    "riscv64": "riscv64gc-unknown-linux-gnu",
}

LINUX_MUSL_TARGETS = {
    "x86_64": "x86_64-unknown-linux-musl",
    "i686": "i686-unknown-linux-musl",
    "aarch64": "aarch64-unknown-linux-musl",
    "armv7": "armv7-unknown-linux-musleabihf",
}

WHEEL_ARCHES = {
    "x86_64": "x86_64",
    "i686": "i686",
    "aarch64": "aarch64",
    "armv7": "armv7l",
    "ppc64le": "ppc64le",
    "s390x": "s390x",
    "riscv64": "riscv64",
}


def _env_truthy(value: Optional[str]) -> bool:
    """Return True when an environment variable value reads as enabled/truthy."""
    if value is None:
        return False
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def _normalize_machine(machine: str) -> str:
    normalized = machine.lower().replace("-", "_")
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86": "i686",
        "i386": "i686",
        "arm64": "aarch64",
        "armv7l": "armv7",
        "powerpc64le": "ppc64le",
        "riscv64gc": "riscv64",
    }
    return aliases.get(normalized, normalized)


def _target_machine(target: str) -> str:
    return _normalize_machine(target.split("-", 1)[0])


def _normalize_platform(platform: str) -> str:
    normalized = platform.lower()
    if normalized.startswith("win"):
        return "win32"
    if normalized.startswith("macosx") or normalized == "darwin":
        return "darwin"
    if normalized.startswith(("linux", "manylinux", "musllinux")):
        return "linux"
    return normalized


def _linux_targets_for_platform(platform: str) -> dict[str, str]:
    if platform.lower().startswith("musllinux"):
        return LINUX_MUSL_TARGETS
    return LINUX_GNU_TARGETS


def _unsupported_machine(platform: str, machine: str, supported: dict[str, str]) -> ValueError:
    supported_machines = ", ".join(sorted(supported))
    return ValueError(f"Unsupported machine type: {machine} for {platform} platform. Supported machines: {supported_machines}.")


def _explicit_target(target: str, *, platform: str, machine: str) -> ResolvedTarget:
    if "manylinux" in target or "musllinux" in target:
        raise ValueError(
            "Rust target triples use linux-gnu or linux-musl, not manylinux or musllinux wheel platform tags. "
            "Use a Rust target such as x86_64-unknown-linux-gnu or x86_64-unknown-linux-musl."
        )
    if "universal2" in target:
        raise ValueError(
            "macOS universal2 is a wheel strategy, not a Rust target triple. Build x86_64-apple-darwin and "
            "aarch64-apple-darwin artifacts separately, then combine them with a project-specific step if needed."
        )

    if target.endswith("-pc-windows-msvc"):
        return ResolvedTarget(platform="win32", machine=_target_machine(target), triple=target)
    if target.endswith("-apple-darwin"):
        return ResolvedTarget(platform="darwin", machine=_target_machine(target), triple=target)
    if "-linux-" in target:
        return ResolvedTarget(platform="linux", machine=_target_machine(target), triple=target)
    return ResolvedTarget(platform=platform, machine=machine, triple=target)


def resolve_target_triple(target: Optional[str] = None, *, platform: Optional[str] = None, machine: Optional[str] = None) -> str:
    """Resolve a Rust target triple from explicit config or host platform details."""
    return _resolve_target(target, platform=platform, machine=machine).triple


def shared_library_name(library: str, *, platform: Optional[str] = None) -> str:
    """Render a platform-specific standalone shared-library filename."""
    platform = _normalize_platform(platform or environ.get("HATCH_RUST_PLATFORM", sys_platform))
    if platform == "win32":
        return f"{library}.dll"
    if platform == "darwin":
        return f"lib{library}.dylib"
    if platform == "linux":
        return f"lib{library}.so"
    raise ValueError(f"Unsupported platform: {platform}")


def python_extension_name(source_stem: str, *, abi3: bool = False, platform: Optional[str] = None) -> str:
    """Render the Python extension filename for a Cargo cdylib artifact stem."""
    platform = _normalize_platform(platform or environ.get("HATCH_RUST_PLATFORM", sys_platform))
    module_name = source_stem.removeprefix("lib")
    if platform == "win32":
        return f"{module_name}.pyd"
    if abi3:
        return f"{module_name}.abi3.so"
    return f"{module_name}.so"


def _resolve_target(target: Optional[str] = None, *, platform: Optional[str] = None, machine: Optional[str] = None) -> ResolvedTarget:
    raw_platform = platform or environ.get("HATCH_RUST_PLATFORM", sys_platform)
    platform = _normalize_platform(raw_platform)
    machine = _normalize_machine(machine or environ.get("HATCH_RUST_MACHINE", platform_machine()))

    if target:
        return _explicit_target(target, platform=platform, machine=machine)

    if platform == "win32":
        try:
            triple = WINDOWS_TARGETS[machine]
        except KeyError as error:
            raise _unsupported_machine("Windows", machine, WINDOWS_TARGETS) from error
    elif platform == "darwin":
        if machine == "universal2":
            raise ValueError(
                "macOS universal2 wheels require separate concrete Rust targets, x86_64-apple-darwin and aarch64-apple-darwin, "
                "plus a project-specific combine step."
            )
        try:
            triple = DARWIN_TARGETS[machine]
        except KeyError as error:
            raise _unsupported_machine("macOS", machine, DARWIN_TARGETS) from error
    elif platform == "linux":
        linux_targets = _linux_targets_for_platform(raw_platform)
        try:
            triple = linux_targets[machine]
        except KeyError as error:
            raise _unsupported_machine("Linux", machine, linux_targets) from error
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    return ResolvedTarget(platform=platform, machine=machine, triple=triple)


def _linux_wheel_platform(resolved_target: ResolvedTarget, platform_tag: Optional[str]) -> str:
    if platform_tag:
        return platform_tag

    auditwheel_platform = environ.get("AUDITWHEEL_PLAT")
    if auditwheel_platform:
        return auditwheel_platform

    arch = WHEEL_ARCHES.get(resolved_target.machine)
    if arch is None:
        raise _unsupported_machine("Linux wheel", resolved_target.machine, WHEEL_ARCHES)
    if "musl" in resolved_target.triple:
        return f"musllinux_1_2_{arch}"
    return f"linux_{arch}"


def _wheel_platform(resolved_target: ResolvedTarget, platform_tag: Optional[str]) -> str:
    if resolved_target.platform == "win32":
        windows_platforms = {
            "x86_64": "win_amd64",
            "i686": "win32",
            "aarch64": "win_arm64",
        }
        try:
            return windows_platforms[resolved_target.machine]
        except KeyError as error:
            raise _unsupported_machine("Windows wheel", resolved_target.machine, windows_platforms) from error
    if resolved_target.platform == "darwin":
        if platform_tag:
            return platform_tag
        darwin_arches = {"x86_64": "x86_64", "aarch64": "arm64"}
        try:
            return next(mac_platforms((11, 0), darwin_arches[resolved_target.machine]))
        except KeyError as error:
            raise _unsupported_machine("macOS wheel", resolved_target.machine, darwin_arches) from error
    if resolved_target.platform == "linux":
        return _linux_wheel_platform(resolved_target, platform_tag)
    raise ValueError(f"Unsupported platform for wheel tag: {resolved_target.platform}")


def wheel_tag(
    *,
    abi3: bool = False,
    target: Optional[str] = None,
    platform: Optional[str] = None,
    machine: Optional[str] = None,
    resolved_target: Optional[ResolvedTarget] = None,
    platform_tag: Optional[str] = None,
    python_version: Optional[tuple[int, int]] = None,
) -> str:
    """Render a wheel tag for the resolved Rust target using packaging.tags."""
    resolved = resolved_target or _resolve_target(target, platform=platform, machine=machine)
    version = python_version or (version_info.major, version_info.minor)
    abis = ["abi3"] if abi3 else None
    return str(next(cpython_tags(python_version=version, abis=abis, platforms=[_wheel_platform(resolved, platform_tag)])))


def _artifact_patterns(platform: str) -> tuple[str, ...]:
    if platform == "win32":
        return ("*.dll", "*.pyd")
    if platform == "linux":
        return ("*.so",)
    if platform == "darwin":
        return ("*.dylib",)
    raise ValueError(f"Unsupported platform machine: {platform_machine()}")


def _cargo_artifact_stem(path: Path) -> str:
    stem = path.stem
    if path.parent.name == "deps":
        stem = sub(r"-[0-9a-f]{16}$", "", stem)
    return stem


def _resolve_path(path: Path | str, *, base: Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path.resolve()
    return (base / path).resolve()


def _cargo_path(path: Path) -> str:
    return path.as_posix()


def _cargo_profile(profile: Optional[str], build_type: BuildType) -> str:
    return profile or build_type


def _cargo_profile_args(profile: str) -> list[str]:
    if profile == "release":
        return ["--release"]
    if profile in ("debug", "dev"):
        return []
    return ["--profile", profile]


def _profile_output_dir(profile: str) -> str:
    if profile in ("debug", "dev"):
        return "debug"
    return profile


class GeneratedOutputConfig(BaseModel):
    """A generated file output and how it should be included in the wheel."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    source: Path = Field(description="Generated or validated source path, relative to the hook path unless absolute.")
    destination: Optional[str] = Field(default=None, description="Wheel-relative destination path or template.")
    install_scheme: InstallScheme = Field(default="package", alias="install-scheme", description="How to include this output.")
    required: bool = Field(default=True, description="Whether the source must exist after the artifact runs.")

    @field_validator("source", mode="before")
    @classmethod
    def validate_source(cls, source: Path) -> Path:
        if not isinstance(source, Path):
            source = Path(source)
        return source


class ValidationCommandConfig(BaseModel):
    """A project-specific command used to validate a built artifact."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    command: List[str] = Field(description="Validation command argv list.")
    working_directory: Optional[Path] = Field(default=None, alias="working-directory", description="Validation command working directory.")
    env: dict[str, str] = Field(default_factory=dict, description="Additional environment variables for this validation command.")

    @field_validator("command", mode="before")
    @classmethod
    def validate_command(cls, values: Any) -> list[str]:
        if isinstance(values, str):
            raise ValueError("Validation command must be an argv list, not a shell string.")
        return [str(value) for value in values]

    @field_validator("working_directory", mode="before")
    @classmethod
    def validate_working_directory(cls, path: Optional[Path]) -> Optional[Path]:
        if path is None:
            return None
        if not isinstance(path, Path):
            path = Path(path)
        return path


class RustArtifactConfig(BaseModel):
    """Configuration for one Rust build artifact."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: Optional[str] = Field(default=None, description="Cargo artifact name used for exact artifact discovery and errors.")
    skip_if_env: Optional[str] = Field(
        default=None,
        alias="skip-if-env",
        description="Skip building and packaging this artifact when the named environment variable is set to a truthy value.",
    )
    manifest: Optional[Path] = Field(default=None, description="Path to Cargo.toml, relative to the hook path unless absolute.")
    build_type: Optional[BuildType] = Field(default=None, alias="build-type")
    profile: Optional[str] = Field(default=None, description="Cargo profile for this artifact.")
    target: Optional[str] = Field(default=None, description="Rust target triple for this artifact.")
    package: Optional[str] = Field(default=None, description="Cargo package selector.")
    cargo_target_kind: Optional[CargoTargetKind] = Field(default=None, alias="cargo-target-kind", description="Cargo target selector kind.")
    cargo_target: Optional[str] = Field(default=None, alias="cargo-target", description="Cargo target selector name.")
    crate_type: str = Field(default="cdylib", alias="crate-type", description="Rust crate type to pass through to rustc.")
    destination: Optional[str] = Field(default=None, description="Wheel-relative destination template for the copied artifact.")
    search_deps: bool = Field(default=False, alias="search-deps", description="Search target/<triple>/<profile>/deps even if a root artifact exists.")
    features: Optional[List[str]] = Field(default=None, description="Cargo features to enable for this artifact.")
    all_features: Optional[bool] = Field(default=None, alias="all-features", description="Enable all Cargo features for this artifact.")
    no_default_features: Optional[bool] = Field(
        default=None,
        alias="no-default-features",
        description="Disable Cargo default features for this artifact.",
    )
    locked: Optional[bool] = Field(default=None, description="Pass --locked to Cargo for this artifact.")
    frozen: Optional[bool] = Field(default=None, description="Pass --frozen to Cargo for this artifact.")
    cargo_args: Optional[List[str]] = Field(default=None, alias="cargo-args", description="Additional Cargo arguments for this artifact.")
    rustc_args: Optional[List[str]] = Field(default=None, alias="rustc-args", description="Additional rustc arguments for this artifact.")
    env: dict[str, str] = Field(default_factory=dict, description="Additional environment variables for this artifact.")
    command: Optional[List[str]] = Field(default=None, description="Command argv for generated-file artifacts.")
    inputs: List[str] = Field(default_factory=list, description="Input files/globs for validation and documentation.")
    outputs: List[GeneratedOutputConfig] = Field(default_factory=list, description="Generated file outputs.")
    working_directory: Optional[Path] = Field(default=None, alias="working-directory", description="Command working directory.")
    generator: Optional[str] = Field(default=None, description="Typed generator preset, currently cbindgen.")
    crate: Optional[str] = Field(default=None, description="Rust crate path for cbindgen CLI mode.")
    config: Optional[Path] = Field(default=None, description="Generator config path, such as cbindgen.toml.")
    language: Optional[str] = Field(default=None, description="Generator language, such as C, C++, or Cython.")
    verify: bool = Field(default=False, description="Run a generator in verification mode when supported.")
    cbindgen_mode: CbindgenMode = Field(default="cli", alias="cbindgen-mode", description="How cbindgen headers are produced.")
    cpp_compat: bool = Field(default=False, alias="cpp-compat", description="Reserved for cbindgen C++ compatibility presets.")
    depfile: Optional[Path] = Field(default=None, description="Reserved depfile path for generated headers.")
    expected_symbols: List[str] = Field(default_factory=list, alias="expected-symbols", description="Exported symbols expected in a copied library.")
    expected_headers: List[Path] = Field(default_factory=list, alias="expected-headers", description="Header files expected to exist after build.")
    expected_abi_strings: List[str] = Field(
        default_factory=list,
        alias="expected-abi-strings",
        description="ABI version strings or macros expected in the configured headers.",
    )
    validate_artifact: bool = Field(default=False, alias="validate", description="Load the copied library with ctypes.CDLL after copy.")
    validation_commands: List[ValidationCommandConfig] = Field(
        default_factory=list,
        alias="validation-commands",
        description="Project-specific validation commands to run after artifact copy.",
    )
    include_import_lib: bool = Field(
        default=False,
        alias="include-import-lib",
        description="On Windows, include the import library emitted next to a cdylib.",
    )
    import_library_destination: Optional[str] = Field(
        default=None,
        alias="import-library-destination",
        description="Wheel-relative destination template for a Windows import library.",
    )

    @field_validator("manifest", mode="before")
    @classmethod
    def validate_manifest(cls, manifest: Optional[Path]) -> Optional[Path]:
        if manifest is None:
            return None
        if not isinstance(manifest, Path):
            manifest = Path(manifest)
        return manifest

    @field_validator("features", "cargo_args", "rustc_args", mode="before")
    @classmethod
    def validate_optional_list(cls, values: Any) -> Optional[list[str]]:
        if values is None:
            return None
        if isinstance(values, str):
            return [values]
        return list(values)

    @field_validator("command", mode="before")
    @classmethod
    def validate_command(cls, values: Any) -> Optional[list[str]]:
        if values is None:
            return None
        if isinstance(values, str):
            raise ValueError("Artifact command must be an argv list, not a shell string.")
        return [str(value) for value in values]

    @field_validator("inputs", mode="before")
    @classmethod
    def validate_inputs(cls, values: Any) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [values]
        return [str(value) for value in values]

    @field_validator("expected_symbols", "expected_abi_strings", mode="before")
    @classmethod
    def validate_string_list(cls, values: Any) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [values]
        return [str(value) for value in values]

    @field_validator("expected_headers", mode="before")
    @classmethod
    def validate_path_list(cls, values: Any) -> list[Path]:
        if values is None:
            return []
        if isinstance(values, (str, Path)):
            values = [values]
        return [value if isinstance(value, Path) else Path(value) for value in values]

    @field_validator("validation_commands", mode="before")
    @classmethod
    def validate_validation_commands(cls, values: Any) -> list[Any]:
        if values is None:
            return []
        if isinstance(values, dict):
            return [values]
        if isinstance(values, list) and values and all(not isinstance(value, dict) for value in values):
            return [{"command": values}]
        return list(values)

    @field_validator("working_directory", "config", "depfile", mode="before")
    @classmethod
    def validate_optional_path(cls, path: Optional[Path]) -> Optional[Path]:
        if path is None:
            return None
        if not isinstance(path, Path):
            path = Path(path)
        return path


@dataclass(frozen=True)
class PlannedArtifact:
    """A generated Cargo invocation plus the metadata needed to collect its output."""

    artifact: RustArtifactConfig
    invocation: Optional[CargoInvocation]
    resolved_target: ResolvedTarget
    profile: str
    target_dir: Path
    set_target_dir_env: bool


class HatchRustBuildConfig(BaseModel):
    """Build config values for Hatch Rust Builder."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    verbose: Optional[bool] = Field(default=False)
    name: Optional[str] = Field(default=None)

    module: str = Field(description="Python module name for the Rust extension.")
    path: Optional[Path] = Field(default=None, description="Path to the project root directory.")
    manifest: Optional[Path] = Field(default=None, description="Path to Cargo.toml, relative to path unless absolute.")
    build_type: BuildType = Field(default="release", alias="build-type")
    profile: Optional[str] = Field(default=None, description="Cargo profile to build. Overrides build_type when set.")
    features: List[str] = Field(default_factory=list, description="Cargo features to enable.")
    all_features: bool = Field(default=False, alias="all-features", description="Enable all Cargo features.")
    no_default_features: bool = Field(default=False, alias="no-default-features", description="Disable Cargo default features.")
    locked: bool = Field(default=False, description="Pass --locked to Cargo.")
    frozen: bool = Field(default=False, description="Pass --frozen to Cargo.")
    cargo_args: List[str] = Field(default_factory=list, alias="cargo-args", description="Additional arguments passed to Cargo.")
    rustc_args: List[str] = Field(default_factory=list, alias="rustc-args", description="Additional arguments passed to rustc.")
    target_dir: Optional[str] = Field(default=None, alias="target-dir", description="Cargo target directory mode or explicit path.")
    env: dict[str, str] = Field(default_factory=dict, description="Additional environment variables for Cargo.")
    artifacts: List[RustArtifactConfig] = Field(default_factory=list, description="Explicit Rust artifacts to build and package.")
    artifact_manifest: bool = Field(
        default=False,
        alias="artifact-manifest",
        description="Emit a package-local JSON manifest describing packaged artifacts.",
    )
    artifact_manifest_destination: str = Field(
        default="{module}/lib/hatch-rs-artifacts.json",
        alias="artifact-manifest-destination",
        description="Wheel-relative destination template for the artifact metadata manifest.",
    )
    wheel_platform_tag: Optional[str] = Field(
        default=None,
        alias="wheel-platform-tag",
        description="Override the wheel platform tag, such as manylinux_2_28_x86_64 or musllinux_1_2_x86_64.",
    )

    abi3: bool = Field(
        default=False,
        description="If True, build the extension with Python's ABI3 compatibility.",
    )

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

    @field_validator("manifest", mode="before")
    @classmethod
    def validate_manifest(cls, manifest: Optional[Path]) -> Optional[Path]:
        if manifest is None:
            return None
        if not isinstance(manifest, Path):
            manifest = Path(manifest)
        return manifest

    @field_validator("features", "cargo_args", "rustc_args", mode="before")
    @classmethod
    def validate_list(cls, values: Any) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [values]
        return list(values)


class HatchRustBuildPlan(HatchRustBuildConfig):
    commands: List[str] = Field(default_factory=list)

    _libraries: List[str] = PrivateAttr(default_factory=list)
    _cargo_invocations: List[CargoInvocation] = PrivateAttr(default_factory=list)
    _artifact_plans: List[PlannedArtifact] = PrivateAttr(default_factory=list)
    _copied_artifacts: List[CopiedArtifact] = PrivateAttr(default_factory=list)
    _shared_data: dict[str, str] = PrivateAttr(default_factory=dict)
    _artifact_manifest_records: list[dict[str, str]] = PrivateAttr(default_factory=list)
    _resolved_target: Optional[ResolvedTarget] = PrivateAttr(default=None)
    _target_dir: Optional[Path] = PrivateAttr(default=None)
    _set_target_dir_env: bool = PrivateAttr(default=False)
    _temporary_target_dir: Optional[TemporaryDirectory[str]] = PrivateAttr(default=None)

    @property
    def libraries(self) -> List[str]:
        return list(self._libraries)

    @property
    def cargo_invocations(self) -> List[CargoInvocation]:
        return list(self._cargo_invocations)

    @property
    def copied_artifacts(self) -> List[CopiedArtifact]:
        return list(self._copied_artifacts)

    @property
    def shared_data(self) -> dict[str, str]:
        return dict(self._shared_data)

    @property
    def resolved_target(self) -> Optional[ResolvedTarget]:
        return self._resolved_target

    def _artifact_skipped(self, artifact: RustArtifactConfig) -> bool:
        return artifact.skip_if_env is not None and _env_truthy(environ.get(artifact.skip_if_env))

    def _configured_artifacts(self) -> list[RustArtifactConfig]:
        if self.artifacts:
            return [artifact for artifact in self.artifacts if not self._artifact_skipped(artifact)]
        return [RustArtifactConfig(destination=f"{self.module}/{{python_extension_name}}")]

    def _artifact_label(self, artifact: RustArtifactConfig) -> str:
        return artifact.name or artifact.destination or "artifact"

    def _artifact_name(self, artifact: RustArtifactConfig) -> str:
        if not artifact.name:
            raise ValueError(f"Artifact '{self._artifact_label(artifact)}' must set name for exact cdylib discovery.")
        return artifact.name

    def _is_generated_artifact(self, artifact: RustArtifactConfig) -> bool:
        if artifact.command is not None or artifact.generator is not None:
            return True
        return bool(artifact.outputs) and artifact.destination is None and artifact.manifest is None

    def _is_python_extension_artifact(self, artifact: RustArtifactConfig) -> bool:
        return artifact.destination is not None and "{python_extension_name}" in artifact.destination

    def _artifact_role(self, artifact: RustArtifactConfig) -> str:
        if self._is_generated_artifact(artifact):
            return "generated-output"
        if self._is_python_extension_artifact(artifact):
            return "python-extension"
        return artifact.crate_type

    def _artifact_manifest(self, artifact: RustArtifactConfig) -> Optional[Path]:
        return artifact.manifest if artifact.manifest is not None else self.manifest

    def _artifact_profile(self, artifact: RustArtifactConfig) -> str:
        build_type = artifact.build_type or self.build_type
        return artifact.profile or self.profile or build_type

    def _artifact_features(self, artifact: RustArtifactConfig) -> list[str]:
        return list(artifact.features if artifact.features is not None else self.features)

    def _artifact_cargo_args(self, artifact: RustArtifactConfig) -> list[str]:
        return list(artifact.cargo_args if artifact.cargo_args is not None else self.cargo_args)

    def _artifact_rustc_args(self, artifact: RustArtifactConfig) -> list[str]:
        return list(artifact.rustc_args if artifact.rustc_args is not None else self.rustc_args)

    def _artifact_bool(self, value: Optional[bool], default: bool) -> bool:
        return default if value is None else value

    def _artifact_env(self, artifact: RustArtifactConfig) -> dict[str, str]:
        environment = {str(key): str(value) for key, value in self.env.items()}
        environment.update({str(key): str(value) for key, value in artifact.env.items()})
        return environment

    def _resolve_target_dir(self, manifest: Optional[Path] = None, artifact_env: Optional[dict[str, str]] = None) -> tuple[Path, bool]:
        path = Path(self.path)
        if self.target_dir == "isolated":
            if self._temporary_target_dir is None:
                self._temporary_target_dir = TemporaryDirectory(prefix="hatch-rs-")
            return Path(self._temporary_target_dir.name), True
        if self.target_dir == "project":
            return _resolve_path("target", base=path), True
        if self.target_dir:
            return _resolve_path(self.target_dir, base=path), True

        artifact_env = artifact_env or self.env
        configured_target_dir = artifact_env.get("CARGO_TARGET_DIR") or environ.get("CARGO_TARGET_DIR")
        if configured_target_dir:
            return _resolve_path(configured_target_dir, base=path), False
        manifest = manifest if manifest is not None else self.manifest
        if manifest is not None:
            return _resolve_path(manifest, base=path).parent / "target", False
        return _resolve_path("target", base=path), False

    def _build_environment(self, artifact_env: dict[str, str], target_dir: Path, set_target_dir_env: bool) -> dict[str, str]:
        environment = {str(key): str(value) for key, value in environ.items()}
        environment.update(artifact_env)
        if set_target_dir_env:
            environment["CARGO_TARGET_DIR"] = str(target_dir)
        return environment

    def _append_cargo_target_selector(self, build_command: list[str], artifact: RustArtifactConfig) -> None:
        if artifact.package:
            build_command.extend(("--package", artifact.package))
        if artifact.cargo_target and artifact.cargo_target_kind is None:
            raise ValueError(f"Artifact '{self._artifact_label(artifact)}' sets cargo-target without cargo-target-kind.")
        if artifact.cargo_target_kind is None:
            return

        build_command.append(f"--{artifact.cargo_target_kind}")
        if artifact.cargo_target_kind == "lib":
            if artifact.cargo_target:
                raise ValueError(f"Artifact '{self._artifact_label(artifact)}' sets a named lib target, but Cargo's --lib selector is unnamed.")
            return
        if not artifact.cargo_target:
            raise ValueError(f"Artifact '{self._artifact_label(artifact)}' must set cargo-target for --{artifact.cargo_target_kind}.")
        build_command.append(artifact.cargo_target)

    def _build_artifact_plan(self, artifact: RustArtifactConfig, *, global_target: Optional[str]) -> PlannedArtifact:
        resolved_target = _resolve_target(artifact.target if artifact.target is not None else global_target)
        profile = self._artifact_profile(artifact)
        manifest = self._artifact_manifest(artifact)
        artifact_env = self._artifact_env(artifact)
        target_dir, set_target_dir_env = self._resolve_target_dir(manifest, artifact_env)

        build_command = ["cargo", "rustc"]
        if manifest is not None:
            build_command.extend(("--manifest-path", _cargo_path(manifest)))
        self._append_cargo_target_selector(build_command, artifact)

        build_command.extend(_cargo_profile_args(profile))
        build_command.extend(("--target", resolved_target.triple))

        features = self._artifact_features(artifact)
        if features:
            build_command.extend(("--features", ",".join(features)))
        if self._artifact_bool(artifact.all_features, self.all_features):
            build_command.append("--all-features")
        if self._artifact_bool(artifact.no_default_features, self.no_default_features):
            build_command.append("--no-default-features")
        if self._artifact_bool(artifact.locked, self.locked):
            build_command.append("--locked")
        if self._artifact_bool(artifact.frozen, self.frozen):
            build_command.append("--frozen")
        build_command.extend(self._artifact_cargo_args(artifact))

        rustc_args = []
        if self._is_python_extension_artifact(artifact) and "apple" in resolved_target.triple:
            rustc_args.extend(("-C", "link-arg=-undefined", "-C", "link-arg=dynamic_lookup"))
        rustc_args.extend(self._artifact_rustc_args(artifact))
        if "--crate-type" not in rustc_args:
            rustc_args.extend(("--crate-type", artifact.crate_type))
        if rustc_args:
            build_command.append("--")
            build_command.extend(rustc_args)

        invocation = CargoInvocation(
            args=tuple(build_command),
            cwd=Path(self.path),
            env=self._build_environment(artifact_env, target_dir, set_target_dir_env),
        )
        return PlannedArtifact(
            artifact=artifact,
            invocation=invocation,
            resolved_target=resolved_target,
            profile=profile,
            target_dir=target_dir,
            set_target_dir_env=set_target_dir_env,
        )

    def _build_command_environment(self, artifact: RustArtifactConfig) -> dict[str, str]:
        environment = {str(key): str(value) for key, value in environ.items()}
        environment.update(self._artifact_env(artifact))
        return environment

    def _cbindgen_language(self, artifact: RustArtifactConfig) -> str:
        language = artifact.language or "C"
        normalized = language.lower()
        if normalized not in ("c", "c++", "cython"):
            raise ValueError(f"Artifact '{self._artifact_label(artifact)}' has unsupported cbindgen language: {language}")
        return normalized

    def _build_cbindgen_command(self, artifact: RustArtifactConfig) -> list[str]:
        outputs = self._artifact_outputs(artifact)
        if not outputs:
            raise ValueError(f"cbindgen artifact '{self._artifact_label(artifact)}' must declare an output.")
        output = outputs[0].source

        command = ["cbindgen"]
        if artifact.config is not None:
            command.extend(("--config", _cargo_path(artifact.config)))
        command.extend(("--lang", self._cbindgen_language(artifact)))
        command.extend(("--output", _cargo_path(output)))
        if artifact.verify:
            command.append("--verify")

        crate = artifact.crate
        if crate is None and artifact.manifest is not None:
            crate = artifact.manifest.parent.as_posix() or "."
        if crate is not None:
            command.append(crate)
        return command

    def _build_command_artifact_plan(self, artifact: RustArtifactConfig, *, global_target: Optional[str]) -> PlannedArtifact:
        resolved_target = _resolve_target(artifact.target if artifact.target is not None else global_target)
        profile = self._artifact_profile(artifact)
        manifest = self._artifact_manifest(artifact)
        target_dir, set_target_dir_env = self._resolve_target_dir(manifest, self._artifact_env(artifact))

        command = artifact.command
        if artifact.generator == "cbindgen" and artifact.cbindgen_mode == "cli" and command is None:
            command = self._build_cbindgen_command(artifact)

        invocation = None
        if command is not None:
            working_directory = _resolve_path(artifact.working_directory or ".", base=Path(self.path))
            invocation = CargoInvocation(args=tuple(command), cwd=working_directory, env=self._build_command_environment(artifact))

        return PlannedArtifact(
            artifact=artifact,
            invocation=invocation,
            resolved_target=resolved_target,
            profile=profile,
            target_dir=target_dir,
            set_target_dir_env=set_target_dir_env,
        )

    def generate(self):
        if self._temporary_target_dir is not None:
            self._temporary_target_dir.cleanup()
            self._temporary_target_dir = None

        self.commands = []
        self._cargo_invocations = []
        self._artifact_plans = []
        self._copied_artifacts = []
        self._shared_data = {}
        self._artifact_manifest_records = []
        self._libraries = []

        global_target = self.target
        for artifact in self._configured_artifacts():
            if self._is_generated_artifact(artifact):
                planned_artifact = self._build_command_artifact_plan(artifact, global_target=global_target)
            else:
                planned_artifact = self._build_artifact_plan(artifact, global_target=global_target)
            self._artifact_plans.append(planned_artifact)
            if planned_artifact.invocation is not None:
                if not self._is_generated_artifact(artifact):
                    self._cargo_invocations.append(planned_artifact.invocation)
                self.commands.append(planned_artifact.invocation.display)

        if self._artifact_plans:
            first_artifact = self._artifact_plans[0]
            self._resolved_target = first_artifact.resolved_target
            self.target = first_artifact.resolved_target.triple
            self._target_dir = first_artifact.target_dir
            self._set_target_dir_env = first_artifact.set_target_dir_env

        return self.commands

    def _target_path(self, planned_artifact: PlannedArtifact) -> Path:
        return planned_artifact.target_dir / planned_artifact.resolved_target.triple / _profile_output_dir(planned_artifact.profile)

    def _require_target_path(self, planned_artifact: PlannedArtifact) -> Path:
        target_path = self._target_path(planned_artifact)
        if not target_path.exists():
            raise FileNotFoundError(f"Target path '{target_path}' does not exist for artifact '{self._artifact_label(planned_artifact.artifact)}'.")
        if not target_path.is_dir():
            raise NotADirectoryError(
                f"Target path '{target_path}' is not a directory for artifact '{self._artifact_label(planned_artifact.artifact)}'."
            )
        return target_path

    def _find_dynamic_artifacts(self, target_path: Path, platform: str) -> list[Path]:
        files = []
        for pattern in _artifact_patterns(platform):
            files.extend(target_path.glob(pattern))
        if not files and (target_path / "deps").is_dir():
            for pattern in _artifact_patterns(platform):
                files.extend((target_path / "deps").glob(pattern))
        return [file for file in files if file.is_file()]

    def _find_exact_artifact(self, target_path: Path, expected_name: str, *, search_deps: bool, artifact: RustArtifactConfig) -> Path:
        expected_path = Path(expected_name)
        candidates = []

        root_candidate = target_path / expected_name
        if root_candidate.is_file():
            candidates.append(root_candidate)

        deps_dir = target_path / "deps"
        if deps_dir.is_dir() and (search_deps or not candidates):
            for candidate in deps_dir.glob(f"*{expected_path.suffix}"):
                if candidate.is_file() and _cargo_artifact_stem(candidate) == expected_path.stem:
                    candidates.append(candidate)

        if not candidates:
            raise FileNotFoundError(
                f"Artifact '{self._artifact_label(artifact)}' did not produce expected file '{expected_name}' under '{target_path}'."
            )
        if len(candidates) > 1:
            rendered_candidates = ", ".join(str(candidate) for candidate in candidates)
            raise RuntimeError(
                f"Artifact '{self._artifact_label(artifact)}' produced multiple files matching '{expected_name}': {rendered_candidates}"
            )
        return candidates[0]

    def _format_destination(
        self,
        template: str,
        *,
        planned_artifact: PlannedArtifact,
        source: Path,
        shared_library: str = "",
        import_library: str = "",
    ) -> Path:
        artifact = planned_artifact.artifact
        python_extension = python_extension_name(_cargo_artifact_stem(source), abi3=self.abi3, platform=planned_artifact.resolved_target.platform)
        values = {
            "module": self.module,
            "target": planned_artifact.resolved_target.triple,
            "profile": planned_artifact.profile,
            "name": artifact.name or "",
            "shared_library": shared_library,
            "import_library": import_library,
            "python_extension_name": python_extension,
        }
        try:
            rendered = template.format(**values)
        except KeyError as error:
            raise ValueError(f"Unknown destination token {{{error.args[0]}}} for artifact '{self._artifact_label(artifact)}'.") from error
        distribution_path = Path(rendered)
        if distribution_path.is_absolute():
            raise ValueError(f"Artifact '{self._artifact_label(artifact)}' destination must be relative: {rendered}")
        return distribution_path

    def _relative_display_path(self, path: Path) -> str:
        resolved = path.resolve()
        for base in (Path(self.path).resolve(), Path.cwd().resolve()):
            try:
                return resolved.relative_to(base).as_posix()
            except ValueError:
                continue
        return str(resolved)

    def _record_packaged_artifact(
        self,
        *,
        planned_artifact: PlannedArtifact,
        source: Path,
        distribution_path: str,
        install_scheme: str,
        role: str,
    ) -> None:
        artifact = planned_artifact.artifact
        manifest = self._artifact_manifest(artifact)
        record = {
            "name": self._artifact_label(artifact),
            "role": role,
            "target": planned_artifact.resolved_target.triple,
            "platform": planned_artifact.resolved_target.platform,
            "profile": planned_artifact.profile,
            "source": self._relative_display_path(source),
            "destination": distribution_path,
            "install_scheme": install_scheme,
        }
        if manifest is not None:
            record["manifest"] = self._relative_display_path(_resolve_path(manifest, base=Path(self.path)))
        if not self._is_generated_artifact(artifact):
            record["crate_type"] = artifact.crate_type
        self._artifact_manifest_records.append(record)

    def _copy_artifact(
        self,
        source: Path,
        distribution_path: Path,
        *,
        build_root: Path,
        is_library: bool = False,
        planned_artifact: Optional[PlannedArtifact] = None,
        role: str = "file",
        install_scheme: str = "package",
    ) -> CopiedArtifact:
        destination = build_root / distribution_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            copy2(source, destination)

        copied_artifact = CopiedArtifact(source=source, destination=destination, distribution_path=distribution_path.as_posix())
        self._copied_artifacts.append(copied_artifact)
        if is_library:
            self._libraries.append(copied_artifact.distribution_path)
        if planned_artifact is not None:
            self._record_packaged_artifact(
                planned_artifact=planned_artifact,
                source=source,
                distribution_path=copied_artifact.distribution_path,
                install_scheme=install_scheme,
                role=role,
            )
        return copied_artifact

    def _copy_python_extension(self, planned_artifact: PlannedArtifact, *, build_root: Path) -> list[CopiedArtifact]:
        artifact = planned_artifact.artifact
        target_path = self._require_target_path(planned_artifact)
        platform = planned_artifact.resolved_target.platform

        if artifact.name:
            expected_name = shared_library_name(artifact.name, platform=platform)
            source = self._find_exact_artifact(target_path, expected_name, search_deps=artifact.search_deps, artifact=artifact)
            template = artifact.destination or f"{self.module}/{{python_extension_name}}"
            distribution_path = self._format_destination(template, planned_artifact=planned_artifact, source=source)
            return [
                self._copy_artifact(
                    source,
                    distribution_path,
                    build_root=build_root,
                    is_library=True,
                    planned_artifact=planned_artifact,
                    role="python-extension",
                )
            ]

        files = self._find_dynamic_artifacts(target_path, platform)
        if not files:
            raise FileNotFoundError(f"No build artifacts found in '{target_path}' for artifact '{self._artifact_label(artifact)}'.")

        copied_artifacts = []
        template = artifact.destination or f"{self.module}/{{python_extension_name}}"
        for source in files:
            distribution_path = self._format_destination(template, planned_artifact=planned_artifact, source=source)
            copied_artifacts.append(
                self._copy_artifact(
                    source,
                    distribution_path,
                    build_root=build_root,
                    is_library=True,
                    planned_artifact=planned_artifact,
                    role="python-extension",
                )
            )
        return copied_artifacts

    def _import_library_names(self, artifact: RustArtifactConfig, shared_library: str) -> tuple[str, ...]:
        artifact_name = self._artifact_name(artifact)

        candidates = (
            f"{shared_library}.lib",
            f"{shared_library}.a",
            f"{artifact_name}.dll.lib",
            f"{artifact_name}.lib",
            f"lib{artifact_name}.dll.a",
        )
        return tuple(dict.fromkeys(candidates))

    def _import_library_patterns(self, artifact: RustArtifactConfig) -> tuple[tuple[str, str], ...]:
        artifact_name = self._artifact_name(artifact)

        return (
            (f"{artifact_name}-*.dll.lib", f"{artifact_name}.dll.lib"),
            (f"{artifact_name}-*.lib", f"{artifact_name}.lib"),
            (f"lib{artifact_name}-*.dll.a", f"lib{artifact_name}.dll.a"),
        )

    def _find_import_library(self, target_path: Path, artifact: RustArtifactConfig, *, shared_library: str) -> tuple[Path, str]:
        candidate_names = self._import_library_names(artifact, shared_library)
        candidates = []
        seen_candidates = set()

        def append_candidate(candidate: Path, import_library: str) -> None:
            resolved_candidate = candidate.resolve()
            if resolved_candidate not in seen_candidates:
                candidates.append((candidate, import_library))
                seen_candidates.add(resolved_candidate)

        for candidate_name in candidate_names:
            candidate = target_path / candidate_name
            if candidate.is_file():
                append_candidate(candidate, candidate_name)
        if not candidates:
            for pattern, import_library in self._import_library_patterns(artifact):
                for candidate in target_path.glob(pattern):
                    if candidate.is_file():
                        append_candidate(candidate, import_library)

        deps_dir = target_path / "deps"
        if deps_dir.is_dir() and (artifact.search_deps or not candidates):
            for candidate_name in candidate_names:
                candidate = deps_dir / candidate_name
                if candidate.is_file():
                    append_candidate(candidate, candidate_name)
            if not candidates:
                for pattern, import_library in self._import_library_patterns(artifact):
                    for candidate in deps_dir.glob(pattern):
                        if candidate.is_file():
                            append_candidate(candidate, import_library)

        if not candidates:
            expected_names = ", ".join(candidate_names)
            raise FileNotFoundError(
                f"Artifact '{self._artifact_label(artifact)}' requested include-import-lib but none of {expected_names} exist under '{target_path}'."
            )
        if len(candidates) > 1:
            rendered_candidates = ", ".join(str(candidate) for candidate, _ in candidates)
            raise RuntimeError(f"Artifact '{self._artifact_label(artifact)}' produced multiple import libraries: {rendered_candidates}")
        return candidates[0]

    def _default_import_library_destination(self, artifact: RustArtifactConfig) -> str:
        if artifact.import_library_destination:
            return artifact.import_library_destination
        if artifact.destination:
            return (Path(artifact.destination).parent / "{import_library}").as_posix()
        return f"{self.module}/lib/{{import_library}}"

    def _copy_import_library(
        self,
        planned_artifact: PlannedArtifact,
        *,
        target_path: Path,
        shared_library: str,
        build_root: Path,
    ) -> Optional[CopiedArtifact]:
        artifact = planned_artifact.artifact
        if not artifact.include_import_lib or planned_artifact.resolved_target.platform != "win32":
            return None

        source, import_library = self._find_import_library(target_path, artifact, shared_library=shared_library)
        template = self._default_import_library_destination(artifact)
        distribution_path = self._format_destination(
            template,
            planned_artifact=planned_artifact,
            source=source,
            shared_library=shared_library,
            import_library=import_library,
        )
        return self._copy_artifact(
            source,
            distribution_path,
            build_root=build_root,
            planned_artifact=planned_artifact,
            role="import-library",
        )

    def _copy_cdylib(self, planned_artifact: PlannedArtifact, *, build_root: Path) -> list[CopiedArtifact]:
        artifact = planned_artifact.artifact
        artifact_name = self._artifact_name(artifact)

        target_path = self._require_target_path(planned_artifact)
        shared_library = shared_library_name(artifact_name, platform=planned_artifact.resolved_target.platform)
        source = self._find_exact_artifact(target_path, shared_library, search_deps=artifact.search_deps, artifact=artifact)
        template = artifact.destination or f"{self.module}/lib/{{shared_library}}"
        distribution_path = self._format_destination(
            template,
            planned_artifact=planned_artifact,
            source=source,
            shared_library=shared_library,
        )
        copied_artifacts = [
            self._copy_artifact(
                source,
                distribution_path,
                build_root=build_root,
                is_library=True,
                planned_artifact=planned_artifact,
                role=artifact.crate_type,
            )
        ]
        import_library = self._copy_import_library(
            planned_artifact,
            target_path=target_path,
            shared_library=shared_library,
            build_root=build_root,
        )
        if import_library is not None:
            copied_artifacts.append(import_library)
        return copied_artifacts

    def _artifact_outputs(self, artifact: RustArtifactConfig) -> list[GeneratedOutputConfig]:
        return list(artifact.outputs)

    def _validate_inputs(self, artifact: RustArtifactConfig) -> None:
        base = Path(self.path)
        for pattern in artifact.inputs:
            path = Path(pattern)
            search_pattern = str(path if path.is_absolute() else base / pattern)
            if not glob(search_pattern, recursive=True):
                raise FileNotFoundError(f"Artifact '{self._artifact_label(artifact)}' input pattern matched no files: {pattern}")

    def _output_destination(self, output: GeneratedOutputConfig, *, planned_artifact: PlannedArtifact, source: Path) -> Path:
        template = output.destination or output.source.as_posix()
        return self._format_destination(template, planned_artifact=planned_artifact, source=source)

    def _process_generated_outputs(self, planned_artifact: PlannedArtifact, *, build_root: Path) -> list[CopiedArtifact]:
        artifact = planned_artifact.artifact
        outputs = self._artifact_outputs(artifact)
        if not outputs:
            raise ValueError(f"Artifact '{self._artifact_label(artifact)}' must declare at least one output.")

        copied_artifacts = []
        for output in outputs:
            source = _resolve_path(output.source, base=Path(self.path))
            if not source.exists():
                if output.required:
                    raise FileNotFoundError(f"Artifact '{self._artifact_label(artifact)}' required output does not exist: {output.source}")
                continue

            if output.install_scheme == "validate-only":
                continue

            distribution_path = self._output_destination(output, planned_artifact=planned_artifact, source=source)
            if output.install_scheme == "package":
                copied_artifacts.append(
                    self._copy_artifact(
                        source,
                        distribution_path,
                        build_root=build_root,
                        planned_artifact=planned_artifact,
                        role="generated-output",
                    )
                )
            elif output.install_scheme == "shared-data":
                self._shared_data[str(source)] = distribution_path.as_posix()
                self._record_packaged_artifact(
                    planned_artifact=planned_artifact,
                    source=source,
                    distribution_path=distribution_path.as_posix(),
                    install_scheme="shared-data",
                    role="generated-output",
                )
            else:
                raise ValueError(f"Unsupported install scheme for artifact '{self._artifact_label(artifact)}': {output.install_scheme}")
        return copied_artifacts

    def _validate_expected_headers(self, artifact: RustArtifactConfig) -> list[Path]:
        headers = []
        for header in artifact.expected_headers:
            resolved_header = _resolve_path(header, base=Path(self.path))
            if not resolved_header.exists():
                raise FileNotFoundError(f"Artifact '{self._artifact_label(artifact)}' expected header does not exist: {header}")
            if not resolved_header.is_file():
                raise FileNotFoundError(f"Artifact '{self._artifact_label(artifact)}' expected header is not a file: {header}")
            headers.append(resolved_header)

        if artifact.expected_abi_strings and not headers:
            raise ValueError(f"Artifact '{self._artifact_label(artifact)}' must set expected-headers when using expected-abi-strings.")

        for expected in artifact.expected_abi_strings:
            if not any(expected in header.read_text(errors="replace") for header in headers):
                raise RuntimeError(f"Artifact '{self._artifact_label(artifact)}' expected ABI string was not found in headers: {expected}")
        return headers

    def _validate_library_exports(self, planned_artifact: PlannedArtifact, copied_artifacts: list[CopiedArtifact]) -> None:
        artifact = planned_artifact.artifact
        if not artifact.validate_artifact and not artifact.expected_symbols:
            return
        if not copied_artifacts:
            raise RuntimeError(f"Artifact '{self._artifact_label(artifact)}' has no copied library to validate.")

        copied_library = copied_artifacts[0]
        try:
            library = CDLL(str(copied_library.destination))
        except OSError as error:
            raise RuntimeError(
                f"Artifact '{self._artifact_label(artifact)}' failed validation load check for '{copied_library.distribution_path}': {error}"
            ) from error

        for symbol in artifact.expected_symbols:
            try:
                getattr(library, symbol)
            except AttributeError as error:
                raise RuntimeError(f"Artifact '{self._artifact_label(artifact)}' expected symbol was not exported: {symbol}") from error

    def _validation_tokens(self, planned_artifact: PlannedArtifact, copied_artifacts: list[CopiedArtifact], headers: list[Path]) -> dict[str, str]:
        artifact = planned_artifact.artifact
        copied_artifact = copied_artifacts[0] if copied_artifacts else None
        shared_library = shared_library_name(artifact.name, platform=planned_artifact.resolved_target.platform) if artifact.name else ""
        return {
            "module": self.module,
            "target": planned_artifact.resolved_target.triple,
            "profile": planned_artifact.profile,
            "name": artifact.name or "",
            "shared_library": shared_library,
            "source": str(copied_artifact.source) if copied_artifact else "",
            "destination": str(copied_artifact.destination) if copied_artifact else "",
            "distribution_path": copied_artifact.distribution_path if copied_artifact else "",
            "header": str(headers[0]) if headers else "",
        }

    def _format_validation_command(self, command: ValidationCommandConfig, *, planned_artifact: PlannedArtifact, tokens: dict[str, str]) -> list[str]:
        args = []
        for value in command.command:
            try:
                args.append(value.format(**tokens))
            except KeyError as error:
                raise ValueError(
                    f"Unknown validation-command token {{{error.args[0]}}} for artifact '{self._artifact_label(planned_artifact.artifact)}'."
                ) from error
        return args

    def _run_validation_commands(
        self,
        planned_artifact: PlannedArtifact,
        *,
        build_root: Path,
        copied_artifacts: list[CopiedArtifact],
        headers: list[Path],
    ) -> None:
        artifact = planned_artifact.artifact
        tokens = self._validation_tokens(planned_artifact, copied_artifacts, headers)
        for validation_command in artifact.validation_commands:
            command = self._format_validation_command(validation_command, planned_artifact=planned_artifact, tokens=tokens)
            working_directory = _resolve_path(validation_command.working_directory or ".", base=Path(self.path))
            environment = self._build_command_environment(artifact)
            environment.update({str(key): str(value).format(**tokens) for key, value in validation_command.env.items()})
            environment.update(
                {
                    "HATCH_RS_ARTIFACT_NAME": self._artifact_label(artifact),
                    "HATCH_RS_ARTIFACT_ROLE": self._artifact_role(artifact),
                    "HATCH_RS_ARTIFACT_CRATE_TYPE": artifact.crate_type,
                    "HATCH_RS_BUILD_ROOT": str(build_root),
                }
            )
            try:
                completed = run(command, cwd=working_directory, env=environment, check=False)
            except FileNotFoundError as error:
                raise RuntimeError(f"Artifact '{self._artifact_label(artifact)}' validation command was not found: {command[0]}") from error
            if completed.returncode != 0:
                raise RuntimeError(
                    f"Artifact '{self._artifact_label(artifact)}' validation command failed with exit code "
                    f"{completed.returncode}: {shell_join(command)}"
                )

    def _validate_artifact(self, planned_artifact: PlannedArtifact, *, build_root: Path, copied_artifacts: list[CopiedArtifact]) -> None:
        headers = self._validate_expected_headers(planned_artifact.artifact)
        self._validate_library_exports(planned_artifact, copied_artifacts)
        self._run_validation_commands(planned_artifact, build_root=build_root, copied_artifacts=copied_artifacts, headers=headers)

    def _format_artifact_manifest_destination(self) -> Path:
        try:
            rendered = self.artifact_manifest_destination.format(
                module=self.module,
                target=self.target or "",
                profile=self.profile or self.build_type,
                name="",
                shared_library="",
                import_library="",
                python_extension_name="",
            )
        except KeyError as error:
            raise ValueError(f"Unknown artifact-manifest-destination token {{{error.args[0]}}}.") from error
        distribution_path = Path(rendered)
        if distribution_path.is_absolute():
            raise ValueError(f"artifact-manifest-destination must be relative: {rendered}")
        return distribution_path

    def _write_artifact_manifest(self, *, build_root: Path) -> None:
        if not self.artifact_manifest:
            return

        distribution_path = self._format_artifact_manifest_destination()
        destination = build_root / distribution_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "module": self.module,
            "artifacts": self._artifact_manifest_records,
        }
        destination.write_text(dumps(payload, indent=2, sort_keys=True) + "\n")
        self._copied_artifacts.append(CopiedArtifact(source=destination, destination=destination, distribution_path=distribution_path.as_posix()))

    def _copy_outputs(self, planned_artifact: PlannedArtifact, *, build_root: Path) -> list[CopiedArtifact]:
        artifact = planned_artifact.artifact
        copied_artifacts = []
        if self._is_generated_artifact(artifact):
            copied_artifacts.extend(self._process_generated_outputs(planned_artifact, build_root=build_root))
        elif self._is_python_extension_artifact(artifact):
            copied_artifacts.extend(self._copy_python_extension(planned_artifact, build_root=build_root))
            if artifact.outputs:
                copied_artifacts.extend(self._process_generated_outputs(planned_artifact, build_root=build_root))
        else:
            copied_artifacts.extend(self._copy_cdylib(planned_artifact, build_root=build_root))
            if artifact.outputs:
                copied_artifacts.extend(self._process_generated_outputs(planned_artifact, build_root=build_root))
        self._validate_artifact(planned_artifact, build_root=build_root, copied_artifacts=copied_artifacts)
        return copied_artifacts

    def execute(self):
        """Execute the build commands."""
        build_root = Path.cwd().resolve()
        self._copied_artifacts = []
        self._shared_data = {}
        self._artifact_manifest_records = []
        self._libraries = []

        artifact_plans = self._artifact_plans
        if not artifact_plans:
            self.generate()
            artifact_plans = self._artifact_plans

        for planned_artifact in artifact_plans:
            self._validate_inputs(planned_artifact.artifact)
            if planned_artifact.invocation is not None:
                try:
                    completed = run(
                        planned_artifact.invocation.args,
                        cwd=planned_artifact.invocation.cwd,
                        env=planned_artifact.invocation.env,
                        check=False,
                    )
                except FileNotFoundError as error:
                    executable = planned_artifact.invocation.args[0]
                    if executable == "cbindgen":
                        raise RuntimeError(
                            f"Artifact '{self._artifact_label(planned_artifact.artifact)}' requires the cbindgen CLI. "
                            "Install cbindgen or use cbindgen-mode = 'build-script'."
                        ) from error
                    raise RuntimeError(f"Artifact '{self._artifact_label(planned_artifact.artifact)}' command was not found: {executable}") from error
                if completed.returncode != 0:
                    raise RuntimeError(f"hatch-rs build command failed with exit code {completed.returncode}: {planned_artifact.invocation.display}")
            self._copy_outputs(planned_artifact, build_root=build_root)

        self._write_artifact_manifest(build_root=build_root)

        return self.commands

    def cleanup(self):
        if self._temporary_target_dir is not None:
            self._temporary_target_dir.cleanup()
            self._temporary_target_dir = None
        # if self.platform.platform == "win32":
        #     for temp_obj in Path(".").glob("*.obj"):
        #         temp_obj.unlink()
