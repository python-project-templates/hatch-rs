from __future__ import annotations

from dataclasses import dataclass
from os import environ
from pathlib import Path
from platform import machine as platform_machine
from re import sub
from shlex import join as shell_join
from shutil import copy2
from subprocess import run
from sys import platform as sys_platform
from tempfile import TemporaryDirectory
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

__all__ = (
    "CargoInvocation",
    "CopiedArtifact",
    "HatchRustBuildConfig",
    "HatchRustBuildPlan",
    "ResolvedTarget",
    "RustArtifactConfig",
    "python_extension_name",
    "resolve_target_triple",
    "shared_library_name",
)

ArtifactKind = Literal["python-extension", "shared-library"]
BuildType = Literal["debug", "release"]
CargoTargetKind = Literal["lib", "bin", "example", "test", "bench"]
CompilerToolchain = Literal["gcc", "clang", "msvc"]
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


def resolve_target_triple(target: Optional[str] = None, *, platform: Optional[str] = None, machine: Optional[str] = None) -> str:
    """Resolve a Rust target triple from explicit config or host platform details."""
    return _resolve_target(target, platform=platform, machine=machine).triple


def shared_library_name(library: str, *, platform: Optional[str] = None) -> str:
    """Render a platform-specific standalone shared-library filename."""
    platform = platform or environ.get("HATCH_RUST_PLATFORM", sys_platform)
    if platform == "win32":
        return f"{library}.dll"
    if platform == "darwin":
        return f"lib{library}.dylib"
    if platform == "linux":
        return f"lib{library}.so"
    raise ValueError(f"Unsupported platform: {platform}")


def python_extension_name(source_stem: str, *, abi3: bool = False, platform: Optional[str] = None) -> str:
    """Render the Python extension filename for a Cargo cdylib artifact stem."""
    platform = platform or environ.get("HATCH_RUST_PLATFORM", sys_platform)
    module_name = source_stem.removeprefix("lib")
    if platform == "win32":
        return f"{module_name}.pyd"
    if abi3:
        return f"{module_name}.abi3.so"
    return f"{module_name}.so"


def _resolve_target(target: Optional[str] = None, *, platform: Optional[str] = None, machine: Optional[str] = None) -> ResolvedTarget:
    platform = platform or environ.get("HATCH_RUST_PLATFORM", sys_platform)
    machine = machine or environ.get("HATCH_RUST_MACHINE", platform_machine())

    if target:
        if target.endswith("-pc-windows-msvc"):
            platform = "win32"
            machine = target.split("-", 1)[0]
        elif target.endswith("-apple-darwin"):
            platform = "darwin"
            machine = target.split("-", 1)[0]
        elif "-unknown-linux-" in target:
            platform = "linux"
            machine = target.split("-", 1)[0]
        return ResolvedTarget(platform=platform, machine=machine, triple=target)

    if platform == "win32":
        if machine in ("x86_64", "AMD64"):
            triple = "x86_64-pc-windows-msvc"
        elif machine == "i686":
            triple = "i686-pc-windows-msvc"
        elif machine in ("arm64", "aarch64"):
            triple = "aarch64-pc-windows-msvc"
        else:
            raise ValueError(f"Unsupported machine type: {machine} for Windows platform")
    elif platform == "darwin":
        if machine == "x86_64":
            triple = "x86_64-apple-darwin"
        elif machine in ("arm64", "aarch64"):
            triple = "aarch64-apple-darwin"
        else:
            raise ValueError(f"Unsupported machine type: {machine} for macOS platform")
    elif platform == "linux":
        if machine == "x86_64":
            triple = "x86_64-unknown-linux-gnu"
        elif machine == "i686":
            triple = "i686-unknown-linux-gnu"
        elif machine in ("arm64", "aarch64"):
            triple = "aarch64-unknown-linux-gnu"
        else:
            raise ValueError(f"Unsupported machine type: {machine} for Linux platform")
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    return ResolvedTarget(platform=platform, machine=machine, triple=triple)


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


class RustArtifactConfig(BaseModel):
    """Configuration for one Rust build artifact."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: Optional[str] = Field(default=None, description="Human-readable artifact name used in errors.")
    kind: ArtifactKind = Field(default="python-extension", description="Kind of artifact to build and package.")
    manifest: Optional[Path] = Field(default=None, description="Path to Cargo.toml, relative to the hook path unless absolute.")
    build_type: Optional[BuildType] = Field(default=None, alias="build-type")
    profile: Optional[str] = Field(default=None, description="Cargo profile for this artifact.")
    target: Optional[str] = Field(default=None, description="Rust target triple for this artifact.")
    package: Optional[str] = Field(default=None, description="Cargo package selector.")
    cargo_target_kind: Optional[CargoTargetKind] = Field(default=None, alias="cargo-target-kind", description="Cargo target selector kind.")
    cargo_target: Optional[str] = Field(default=None, alias="cargo-target", description="Cargo target selector name.")
    library: Optional[str] = Field(default=None, description="Cargo library name used for exact artifact discovery.")
    crate_type: Optional[str] = Field(default=None, alias="crate-type", description="Rust crate type to pass through to rustc.")
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


@dataclass(frozen=True)
class PlannedArtifact:
    """A generated Cargo invocation plus the metadata needed to collect its output."""

    artifact: RustArtifactConfig
    invocation: CargoInvocation
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

    def _configured_artifacts(self) -> list[RustArtifactConfig]:
        if self.artifacts:
            return list(self.artifacts)
        return [RustArtifactConfig(name="python-extension", kind="python-extension")]

    def _artifact_label(self, artifact: RustArtifactConfig) -> str:
        return artifact.name or artifact.library or artifact.kind

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
        if artifact.kind == "shared-library" and not artifact.library:
            raise ValueError(f"Shared-library artifact '{self._artifact_label(artifact)}' must set library.")

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
        if artifact.kind == "python-extension" and "apple" in resolved_target.triple:
            rustc_args.extend(("-C", "link-arg=-undefined", "-C", "link-arg=dynamic_lookup"))
        rustc_args.extend(self._artifact_rustc_args(artifact))
        if artifact.crate_type and "--crate-type" not in rustc_args:
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

    def generate(self):
        if self._temporary_target_dir is not None:
            self._temporary_target_dir.cleanup()
            self._temporary_target_dir = None

        self.commands = []
        self._cargo_invocations = []
        self._artifact_plans = []
        self._copied_artifacts = []
        self._libraries = []

        global_target = self.target
        for artifact in self._configured_artifacts():
            planned_artifact = self._build_artifact_plan(artifact, global_target=global_target)
            self._artifact_plans.append(planned_artifact)
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

    def _format_destination(self, template: str, *, planned_artifact: PlannedArtifact, source: Path, shared_library: str = "") -> Path:
        artifact = planned_artifact.artifact
        python_extension = python_extension_name(_cargo_artifact_stem(source), abi3=self.abi3, platform=planned_artifact.resolved_target.platform)
        values = {
            "module": self.module,
            "target": planned_artifact.resolved_target.triple,
            "profile": planned_artifact.profile,
            "library": artifact.library or "",
            "shared_library": shared_library,
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

    def _copy_artifact(self, source: Path, distribution_path: Path, *, build_root: Path) -> None:
        destination = build_root / distribution_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, destination)

        copied_artifact = CopiedArtifact(source=source, destination=destination, distribution_path=distribution_path.as_posix())
        self._copied_artifacts.append(copied_artifact)
        self._libraries.append(copied_artifact.distribution_path)

    def _copy_python_extension(self, planned_artifact: PlannedArtifact, *, build_root: Path) -> None:
        artifact = planned_artifact.artifact
        target_path = self._require_target_path(planned_artifact)
        platform = planned_artifact.resolved_target.platform

        if artifact.library:
            expected_name = shared_library_name(artifact.library, platform=platform)
            source = self._find_exact_artifact(target_path, expected_name, search_deps=artifact.search_deps, artifact=artifact)
            template = artifact.destination or f"{self.module}/{{python_extension_name}}"
            distribution_path = self._format_destination(template, planned_artifact=planned_artifact, source=source)
            self._copy_artifact(source, distribution_path, build_root=build_root)
            return

        files = self._find_dynamic_artifacts(target_path, platform)
        if not files:
            raise FileNotFoundError(f"No build artifacts found in '{target_path}' for artifact '{self._artifact_label(artifact)}'.")

        template = artifact.destination or f"{self.module}/{{python_extension_name}}"
        for source in files:
            distribution_path = self._format_destination(template, planned_artifact=planned_artifact, source=source)
            self._copy_artifact(source, distribution_path, build_root=build_root)

    def _copy_shared_library(self, planned_artifact: PlannedArtifact, *, build_root: Path) -> None:
        artifact = planned_artifact.artifact
        if not artifact.library:
            raise ValueError(f"Shared-library artifact '{self._artifact_label(artifact)}' must set library.")

        target_path = self._require_target_path(planned_artifact)
        shared_library = shared_library_name(artifact.library, platform=planned_artifact.resolved_target.platform)
        source = self._find_exact_artifact(target_path, shared_library, search_deps=artifact.search_deps, artifact=artifact)
        template = artifact.destination or f"{self.module}/lib/{{shared_library}}"
        distribution_path = self._format_destination(
            template,
            planned_artifact=planned_artifact,
            source=source,
            shared_library=shared_library,
        )
        self._copy_artifact(source, distribution_path, build_root=build_root)

    def _copy_outputs(self, planned_artifact: PlannedArtifact, *, build_root: Path) -> None:
        if planned_artifact.artifact.kind == "python-extension":
            self._copy_python_extension(planned_artifact, build_root=build_root)
        elif planned_artifact.artifact.kind == "shared-library":
            self._copy_shared_library(planned_artifact, build_root=build_root)
        else:
            raise ValueError(f"Unsupported artifact kind: {planned_artifact.artifact.kind}")

    def execute(self):
        """Execute the build commands."""
        build_root = Path.cwd().resolve()
        self._copied_artifacts = []
        self._libraries = []

        artifact_plans = self._artifact_plans
        if not artifact_plans:
            self.generate()
            artifact_plans = self._artifact_plans

        for planned_artifact in artifact_plans:
            completed = run(planned_artifact.invocation.args, cwd=planned_artifact.invocation.cwd, env=planned_artifact.invocation.env, check=False)
            if completed.returncode != 0:
                raise RuntimeError(f"hatch-rs build command failed with exit code {completed.returncode}: {planned_artifact.invocation.display}")
            self._copy_outputs(planned_artifact, build_root=build_root)

        return self.commands

    def cleanup(self):
        if self._temporary_target_dir is not None:
            self._temporary_target_dir.cleanup()
            self._temporary_target_dir = None
        # if self.platform.platform == "win32":
        #     for temp_obj in Path(".").glob("*.obj"):
        #         temp_obj.unlink()
