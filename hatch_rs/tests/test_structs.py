from __future__ import annotations

from json import loads
from pathlib import Path, PureWindowsPath

import pytest

from hatch_rs.structs import HatchRustBuildPlan, python_extension_name, resolve_target_triple, shared_library_name, wheel_tag


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
        ("linux", "armv7l", "armv7-unknown-linux-gnueabihf"),
        ("linux", "ppc64le", "powerpc64le-unknown-linux-gnu"),
        ("linux", "s390x", "s390x-unknown-linux-gnu"),
        ("linux", "riscv64", "riscv64gc-unknown-linux-gnu"),
        ("musllinux_1_2_x86_64", "x86_64", "x86_64-unknown-linux-musl"),
    ],
)
def test_resolve_target_triple(platform: str, machine: str, expected: str):
    assert resolve_target_triple(platform=platform, machine=machine) == expected


def test_resolve_target_triple_uses_explicit_target():
    assert resolve_target_triple("wasm32-unknown-unknown", platform="linux", machine="x86_64") == "wasm32-unknown-unknown"


def test_resolve_target_triple_uses_explicit_musl_target():
    assert resolve_target_triple("aarch64-unknown-linux-musl", platform="linux", machine="x86_64") == "aarch64-unknown-linux-musl"


def test_resolve_target_triple_rejects_wheel_platform_tag_as_rust_target():
    with pytest.raises(ValueError, match="manylinux.*wheel platform tags"):
        resolve_target_triple("x86_64-manylinux_2_28")


def test_resolve_target_triple_rejects_macos_universal2_as_rust_target():
    with pytest.raises(ValueError, match="universal2"):
        resolve_target_triple(platform="darwin", machine="universal2")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"abi3": True, "target": "x86_64-unknown-linux-gnu"}, "cp311-abi3-linux_x86_64"),
        ({"target": "aarch64-apple-darwin"}, "cp311-cp311-macosx_11_0_arm64"),
        ({"target": "x86_64-pc-windows-msvc"}, "cp311-cp311-win_amd64"),
        ({"abi3": True, "target": "aarch64-unknown-linux-musl"}, "cp311-abi3-musllinux_1_2_aarch64"),
        ({"target": "x86_64-unknown-linux-gnu", "platform_tag": "manylinux_2_28_x86_64"}, "cp311-cp311-manylinux_2_28_x86_64"),
    ],
)
def test_wheel_tag_uses_packaging_tags(monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, object], expected: str):
    monkeypatch.delenv("AUDITWHEEL_PLAT", raising=False)
    assert wheel_tag(python_version=(3, 11), **kwargs) == expected


def test_wheel_tag_uses_auditwheel_platform(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUDITWHEEL_PLAT", "manylinux_2_28_x86_64")

    assert wheel_tag(target="x86_64-unknown-linux-gnu", python_version=(3, 11)) == "cp311-cp311-manylinux_2_28_x86_64"


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", "mylib.dll"),
        ("darwin", "libmylib.dylib"),
        ("linux", "libmylib.so"),
        ("manylinux_2_28_x86_64", "libmylib.so"),
        ("musllinux_1_2_x86_64", "libmylib.so"),
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


def test_build_plan_processes_generated_outputs(tmp_path):
    package_source = tmp_path / "project" / "generated" / "package.txt"
    shared_source = tmp_path / "project" / "include" / "project.h"
    validated_source = tmp_path / "project" / "generated" / "validated.txt"
    package_source.parent.mkdir(parents=True)
    shared_source.parent.mkdir(parents=True)
    package_source.write_text("package output")
    shared_source.write_text("shared output")
    validated_source.write_text("validated output")

    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        artifacts=[
            {
                "name": "generated-files",
                "kind": "command",
                "command": ["python", "-c", "print('already generated')"],
                "outputs": [
                    {"source": "project/generated/package.txt", "destination": "project/generated/package.txt"},
                    {
                        "source": "project/include/project.h",
                        "destination": "include/project/project.h",
                        "install-scheme": "shared-data",
                    },
                    {"source": "project/generated/validated.txt", "install-scheme": "validate-only"},
                ],
            }
        ],
    )
    plan.generate()
    plan._copy_outputs(plan._artifact_plans[0], build_root=tmp_path)

    assert plan.copied_artifacts[0].distribution_path == "project/generated/package.txt"
    assert plan.shared_data == {str(shared_source.resolve()): "include/project/project.h"}
    assert plan.libraries == []


def test_build_plan_missing_generated_output_fails_clearly(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        artifacts=[
            {
                "name": "missing-header",
                "kind": "header",
                "source": "project/include/project.h",
            }
        ],
    )
    plan.generate()

    with pytest.raises(FileNotFoundError, match="missing-header"):
        plan._copy_outputs(plan._artifact_plans[0], build_root=tmp_path)


def test_build_plan_generates_cbindgen_command(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        artifacts=[
            {
                "name": "project-header",
                "kind": "header",
                "generator": "cbindgen",
                "crate": "rust",
                "config": "rust/cbindgen.toml",
                "language": "C++",
                "output": "project/include/project.h",
                "destination": "include/project/project.h",
                "verify": True,
            }
        ],
    )

    assert plan.generate() == ["cbindgen --config rust/cbindgen.toml --lang c++ --output project/include/project.h --verify rust"]


def test_build_plan_validates_expected_header_strings(tmp_path):
    header = tmp_path / "project" / "include" / "project.h"
    header.parent.mkdir(parents=True)
    header.write_text("#define PROJECT_ABI_VERSION 1\nint project_ffi_answer(void);\n")

    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="x86_64-unknown-linux-gnu",
        artifacts=[
            {
                "name": "c-abi",
                "kind": "shared-library",
                "library": "project_ffi",
                "expected-headers": ["project/include/project.h"],
                "expected-abi-strings": ["PROJECT_ABI_VERSION", "project_ffi_answer"],
            }
        ],
    )
    plan.generate()
    planned_artifact = plan._artifact_plans[0]
    source = tmp_path / "target" / "x86_64-unknown-linux-gnu" / "release" / "libproject_ffi.so"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"shared library")

    plan._copy_outputs(planned_artifact, build_root=tmp_path)

    assert (tmp_path / "project" / "lib" / "libproject_ffi.so").read_bytes() == b"shared library"


def test_build_plan_missing_expected_header_string_fails_clearly(tmp_path):
    header = tmp_path / "project" / "include" / "project.h"
    header.parent.mkdir(parents=True)
    header.write_text("int project_ffi_answer(void);\n")

    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="x86_64-unknown-linux-gnu",
        artifacts=[
            {
                "name": "c-abi",
                "kind": "shared-library",
                "library": "project_ffi",
                "expected-headers": ["project/include/project.h"],
                "expected-abi-strings": ["PROJECT_ABI_VERSION"],
            }
        ],
    )
    plan.generate()
    planned_artifact = plan._artifact_plans[0]
    source = tmp_path / "target" / "x86_64-unknown-linux-gnu" / "release" / "libproject_ffi.so"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"shared library")

    with pytest.raises(RuntimeError, match="PROJECT_ABI_VERSION"):
        plan._copy_outputs(planned_artifact, build_root=tmp_path)


def test_build_plan_copies_windows_import_library(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="x86_64-pc-windows-msvc",
        artifacts=[
            {
                "name": "c-abi",
                "kind": "shared-library",
                "library": "project_ffi",
                "destination": "{module}/lib/{shared_library}",
                "include-import-lib": True,
            }
        ],
    )
    plan.generate()
    planned_artifact = plan._artifact_plans[0]
    target_path = tmp_path / "target" / "x86_64-pc-windows-msvc" / "release"
    target_path.mkdir(parents=True)
    (target_path / "project_ffi.dll").write_bytes(b"shared library")
    (target_path / "project_ffi.dll.lib").write_bytes(b"import library")

    plan._copy_outputs(planned_artifact, build_root=tmp_path)

    assert (tmp_path / "project" / "lib" / "project_ffi.dll").read_bytes() == b"shared library"
    assert (tmp_path / "project" / "lib" / "project_ffi.dll.lib").read_bytes() == b"import library"
    assert plan.libraries == ["project/lib/project_ffi.dll"]


def test_build_plan_copies_windows_import_library_from_deps(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="x86_64-pc-windows-msvc",
        artifacts=[
            {
                "name": "c-abi",
                "kind": "shared-library",
                "library": "project_ffi",
                "destination": "{module}/lib/{shared_library}",
                "include-import-lib": True,
            }
        ],
    )
    plan.generate()
    planned_artifact = plan._artifact_plans[0]
    target_path = tmp_path / "target" / "x86_64-pc-windows-msvc" / "release"
    deps_path = target_path / "deps"
    deps_path.mkdir(parents=True)
    (target_path / "project_ffi.dll").write_bytes(b"shared library")
    (deps_path / "project_ffi-1234567890abcdef.dll.lib").write_bytes(b"import library")

    plan._copy_outputs(planned_artifact, build_root=tmp_path)

    assert (tmp_path / "project" / "lib" / "project_ffi.dll").read_bytes() == b"shared library"
    assert (tmp_path / "project" / "lib" / "project_ffi.dll.lib").read_bytes() == b"import library"
    assert plan.libraries == ["project/lib/project_ffi.dll"]


def test_build_plan_writes_artifact_manifest(tmp_path):
    plan = HatchRustBuildPlan(
        module="project",
        path=tmp_path,
        target="x86_64-unknown-linux-gnu",
        artifact_manifest=True,
        artifacts=[
            {
                "name": "c-abi",
                "kind": "shared-library",
                "manifest": "rust/Cargo.toml",
                "library": "project_ffi",
            }
        ],
    )
    plan.generate()
    planned_artifact = plan._artifact_plans[0]
    source = tmp_path / "rust" / "target" / "x86_64-unknown-linux-gnu" / "release" / "libproject_ffi.so"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"shared library")

    plan._copy_outputs(planned_artifact, build_root=tmp_path)
    plan._write_artifact_manifest(build_root=tmp_path)

    manifest_path = tmp_path / "project" / "lib" / "hatch-rs-artifacts.json"
    payload = loads(manifest_path.read_text())
    assert payload["module"] == "project"
    assert payload["artifacts"] == [
        {
            "destination": "project/lib/libproject_ffi.so",
            "install_scheme": "package",
            "kind": "shared-library",
            "manifest": "rust/Cargo.toml",
            "name": "c-abi",
            "platform": "linux",
            "profile": "release",
            "role": "shared-library",
            "source": "rust/target/x86_64-unknown-linux-gnu/release/libproject_ffi.so",
            "target": "x86_64-unknown-linux-gnu",
        }
    ]
    assert plan.copied_artifacts[-1].distribution_path == "project/lib/hatch-rs-artifacts.json"
