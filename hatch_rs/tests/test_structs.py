from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest

from hatch_rs.structs import HatchRustBuildPlan, python_extension_name, resolve_target_triple, shared_library_name


@pytest.mark.parametrize(
    ("platform", "machine", "expected"),
    [
        ("win32", "x86_64", "x86_64-pc-windows-msvc"),
        ("win32", "AMD64", "x86_64-pc-windows-msvc"),
        ("win32", "i686", "i686-pc-windows-msvc"),
        ("win32", "aarch64", "aarch64-pc-windows-msvc"),
        ("darwin", "x86_64", "x86_64-apple-darwin"),
        ("darwin", "arm64", "aarch64-apple-darwin"),
        ("linux", "x86_64", "x86_64-unknown-linux-gnu"),
        ("linux", "i686", "i686-unknown-linux-gnu"),
        ("linux", "aarch64", "aarch64-unknown-linux-gnu"),
    ],
)
def test_resolve_target_triple(platform: str, machine: str, expected: str):
    assert resolve_target_triple(platform=platform, machine=machine) == expected


def test_resolve_target_triple_uses_explicit_target():
    assert resolve_target_triple("wasm32-unknown-unknown", platform="linux", machine="x86_64") == "wasm32-unknown-unknown"


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", "mylib.dll"),
        ("darwin", "libmylib.dylib"),
        ("linux", "libmylib.so"),
    ],
)
def test_shared_library_name(platform: str, expected: str):
    assert shared_library_name("mylib", platform=platform) == expected


@pytest.mark.parametrize(
    ("source_stem", "platform", "abi3", "expected"),
    [
        ("libproject", "linux", True, "project.abi3.so"),
        ("project", "linux", False, "project.so"),
        ("libproject", "darwin", True, "project.abi3.so"),
        ("project", "win32", True, "project.pyd"),
    ],
)
def test_python_extension_name(source_stem: str, platform: str, abi3: bool, expected: str):
    assert python_extension_name(source_stem, abi3=abi3, platform=platform) == expected


def test_build_plan_generates_cargo_invocation(tmp_path):
    plan = HatchRustBuildPlan(module="project", path=tmp_path, target="x86_64-apple-darwin")

    assert plan.generate() == ["cargo rustc --release --target x86_64-apple-darwin -- -C link-arg=-undefined -C link-arg=dynamic_lookup"]


def test_build_plan_generates_manifest_and_cargo_options(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        manifest=Path("rust/Cargo.toml"),
        target="x86_64-unknown-linux-gnu",
        features=["extension-module"],
        no_default_features=True,
        locked=True,
        frozen=True,
        cargo_args=["--color", "never"],
        rustc_args=["--crate-type", "cdylib"],
    )

    assert plan.generate() == [
        "cargo rustc --manifest-path rust/Cargo.toml --release --target x86_64-unknown-linux-gnu "
        "--features extension-module --no-default-features --locked --frozen --color never -- --crate-type cdylib"
    ]


def test_build_plan_generates_portable_manifest_path_display(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        manifest=PureWindowsPath("rust/Cargo.toml"),
        target="x86_64-unknown-linux-gnu",
    )

    assert plan.generate() == ["cargo rustc --manifest-path rust/Cargo.toml --release --target x86_64-unknown-linux-gnu"]


def test_build_plan_project_target_dir_sets_cargo_env(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="x86_64-unknown-linux-gnu",
        target_dir="project",
        env={"CARGO_TERM_COLOR": "never", "HATCH_RS_TEST_ENV": "enabled"},
    )

    plan.generate()
    invocation = plan.cargo_invocations[0]

    assert invocation.env["CARGO_TERM_COLOR"] == "never"
    assert invocation.env["HATCH_RS_TEST_ENV"] == "enabled"
    assert invocation.env["CARGO_TARGET_DIR"] == str((tmp_path / "target").resolve())


def test_build_plan_explicit_target_dir_sets_cargo_env(tmp_path):
    plan = HatchRustBuildPlan(module="project", path=tmp_path, target="x86_64-unknown-linux-gnu", target_dir="build/cargo-target")

    plan.generate()
    invocation = plan.cargo_invocations[0]

    assert invocation.env["CARGO_TARGET_DIR"] == str((tmp_path / "build" / "cargo-target").resolve())


def test_build_plan_isolated_target_dir_is_cleaned(tmp_path):
    plan = HatchRustBuildPlan(module="project", path=tmp_path, target="x86_64-unknown-linux-gnu", target_dir="isolated")

    plan.generate()
    target_dir = Path(plan.cargo_invocations[0].env["CARGO_TARGET_DIR"])

    assert target_dir.exists()
    plan.cleanup()
    assert not target_dir.exists()


def test_build_plan_generates_explicit_artifacts(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="x86_64-unknown-linux-gnu",
        target_dir="isolated",
        artifacts=[
            {
                "name": "python-extension",
                "kind": "python-extension",
                "manifest": "Cargo.toml",
                "library": "project",
            },
            {
                "name": "c-abi",
                "kind": "shared-library",
                "manifest": "rust/Cargo.toml",
                "library": "project_ffi",
                "crate-type": "cdylib",
                "destination": "{module}/lib/{shared_library}",
                "rustc-args": ["-C", "opt-level=3"],
            },
        ],
    )

    assert plan.generate() == [
        "cargo rustc --manifest-path Cargo.toml --release --target x86_64-unknown-linux-gnu",
        "cargo rustc --manifest-path rust/Cargo.toml --release --target x86_64-unknown-linux-gnu -- -C opt-level=3 --crate-type cdylib",
    ]

    first_target_dir = plan.cargo_invocations[0].env["CARGO_TARGET_DIR"]
    second_target_dir = plan.cargo_invocations[1].env["CARGO_TARGET_DIR"]
    assert first_target_dir == second_target_dir

    plan.cleanup()


def test_build_plan_copies_shared_library_to_tokenized_destination(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="x86_64-unknown-linux-gnu",
        artifacts=[
            {
                "name": "c-abi",
                "kind": "shared-library",
                "library": "project_ffi",
                "destination": "{module}/lib/{target}/{profile}/{shared_library}",
            }
        ],
    )
    plan.generate()
    planned_artifact = plan._artifact_plans[0]
    source = tmp_path / "target" / "x86_64-unknown-linux-gnu" / "release" / "libproject_ffi.so"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"shared library")

    plan._copy_outputs(planned_artifact, build_root=tmp_path)

    copied = tmp_path / "project" / "lib" / "x86_64-unknown-linux-gnu" / "release" / "libproject_ffi.so"
    assert copied.read_bytes() == b"shared library"
    assert plan.libraries == ["project/lib/x86_64-unknown-linux-gnu/release/libproject_ffi.so"]


def test_build_plan_uses_explicit_target_for_shared_library_name(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="aarch64-apple-darwin",
        artifacts=[
            {
                "name": "c-abi",
                "kind": "shared-library",
                "library": "project_ffi",
            }
        ],
    )
    plan.generate()
    planned_artifact = plan._artifact_plans[0]
    source = tmp_path / "target" / "aarch64-apple-darwin" / "release" / "libproject_ffi.dylib"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"shared library")

    plan._copy_outputs(planned_artifact, build_root=tmp_path)

    copied = tmp_path / "project" / "lib" / "libproject_ffi.dylib"
    assert copied.read_bytes() == b"shared library"
    assert plan.libraries == ["project/lib/libproject_ffi.dylib"]
