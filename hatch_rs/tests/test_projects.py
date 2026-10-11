from importlib import import_module
from json import loads
from os import environ, listdir, pathsep
from os.path import dirname
from pathlib import Path
from shutil import copyfile, rmtree, which
from subprocess import PIPE, STDOUT, check_call, run
from sys import executable, modules, path, platform, version_info
from zipfile import ZipFile

import pytest

from hatch_rs.structs import resolve_target_triple, shared_library_name

REPO_ROOT = Path(__file__).resolve().parents[2]


def _subprocess_env():
    env = environ.copy()
    pythonpath = str(REPO_ROOT)
    if env.get("PYTHONPATH"):
        pythonpath = f"{pythonpath}{pathsep}{env['PYTHONPATH']}"
    env["PYTHONPATH"] = pythonpath
    env["PATH"] = f"{dirname(executable)}{pathsep}{env['PATH']}"
    return env


class TestProject:
    @pytest.mark.skipif(which("cargo-bundle-licenses") is None, reason="cargo-bundle-licenses CLI is not installed")
    @pytest.mark.parametrize("workspace", [False, True])
    def test_cargo_licenses_in_wheel_built_from_sdist(self, tmp_path, workspace):
        package = tmp_path / "license_project"
        package.mkdir()
        (package / "__init__.py").write_text("")
        rust = tmp_path / "rust"
        crate = rust / "app" if workspace else rust
        (crate / "src").mkdir(parents=True)
        (crate / "src/lib.rs").write_text('#[no_mangle]\npub extern "C" fn answer() -> u32 { licensed_dependency::answer() }\n')
        deps = "../deps" if workspace else "deps"
        (crate / "Cargo.toml").write_text(
            '[package]\nname = "license_project"\nversion = "0.1.0"\nedition = "2021"\n'
            '[lib]\ncrate-type = ["cdylib"]\n'
            f'[dependencies]\nlicensed-dependency = {{ path = "{deps}/licensed-dependency" }}\n'
            f'extra-dependency = {{ path = "{deps}/extra-dependency", optional = true }}\n'
            '[features]\nextra = ["dep:extra-dependency"]\n'
        )
        if workspace:
            (rust / "Cargo.toml").write_text('[workspace]\nmembers = ["app", "deps/*"]\nresolver = "2"\n')
        for name in ("licensed-dependency", "extra-dependency"):
            dependency = rust / "deps" / name
            (dependency / "src").mkdir(parents=True)
            (dependency / "Cargo.toml").write_text(f'[package]\nname = "{name}"\nversion = "1.0.0"\nedition = "2021"\nlicense = "Apache-2.0"\n')
            (dependency / "src/lib.rs").write_text("pub fn answer() -> u32 { 42 }\n")
            copyfile(REPO_ROOT / "LICENSE", dependency / "LICENSE")
        (tmp_path / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["hatchling", "hatch-rs"]\nbuild-backend = "hatchling.build"\n'
            '[project]\nname = "license-project"\nversion = "0.1.0"\n'
            '[tool.hatch.build.targets.wheel]\npackages = ["license_project"]\n'
            '[tool.hatch.build.targets.sdist]\ninclude = ["license_project", "rust", "pyproject.toml"]\n'
            '[tool.hatch.build.hooks.hatch-rs]\nmodule = "license_project"\npath = "rust"\n'
            'target-dir = "isolated"\nall-features = true\nartifact-manifest = true\n'
            "[tool.hatch.build.hooks.hatch-rs.licenses]\n"
        )
        if workspace:
            with (tmp_path / "pyproject.toml").open("a") as config:
                config.write(
                    '[[tool.hatch.build.hooks.hatch-rs.artifacts]]\nname = "license_project"\npackage = "license_project"\n'
                    'destination = "license_project/{python_extension_name}"\n'
                )

        check_call([executable, "-m", "build", "-n"], cwd=tmp_path, env=_subprocess_env())

        bundle_name = "license_project" if workspace else "cargo"
        with ZipFile(next((tmp_path / "dist").glob("*.whl"))) as wheel:
            bundle = loads(wheel.read(f"license_project/licenses/{bundle_name}.json"))
            manifest = loads(wheel.read("license_project/lib/hatch-rs-artifacts.json"))
        assert {library["package_name"] for library in bundle["third_party_libraries"]} == {"licensed-dependency", "extra-dependency"}
        for library in bundle["third_party_libraries"]:
            assert library["licenses"][0]["text"] == (REPO_ROOT / "LICENSE").read_text()
        assert any(record["role"] == "third-party-licenses" for record in manifest["artifacts"])

    @pytest.mark.parametrize(
        "project_folder",
        [
            "test_project_basic",
        ],
    )
    def test_basic(self, project_folder):
        # cleanup
        rmtree(f"hatch_rs/tests/{project_folder}/dist", ignore_errors=True)
        rmtree(f"hatch_rs/tests/{project_folder}/target", ignore_errors=True)
        rmtree(f"hatch_rs/tests/{project_folder}/project/extension.so", ignore_errors=True)
        rmtree(f"hatch_rs/tests/{project_folder}/project/extension.pyd", ignore_errors=True)
        modules.pop("project", None)
        modules.pop("project.extension", None)

        # compile
        check_call(
            [
                executable,
                "-m",
                "hatchling",
                "build",
                "--hooks-only",
            ],
            cwd=f"hatch_rs/tests/{project_folder}",
            env=_subprocess_env(),
        )

        # assert built
        if platform == "win32":
            assert "project.pyd" in listdir(f"hatch_rs/tests/{project_folder}/project")
        else:
            assert "project.abi3.so" in listdir(f"hatch_rs/tests/{project_folder}/project")

        # dist
        check_call(
            [
                executable,
                "-m",
                "build",
                "-w",
                "-n",
            ],
            cwd=f"hatch_rs/tests/{project_folder}",
            env=_subprocess_env(),
        )

        assert f"cp3{version_info.minor}-abi3" in listdir(f"hatch_rs/tests/{project_folder}/dist")[0]

        # import
        here = Path(__file__).parent / project_folder
        path.insert(0, str(here))
        project = import_module("project.project")

        assert project.hello() == "A string"

    def test_cargo_controls_manifest_rustc_args_and_isolated_target_dir(self):
        project_folder = "test_project_cargo_controls"
        project_root = Path("hatch_rs/tests") / project_folder
        package_dir = project_root / "cargo_controls_project"

        # cleanup
        rmtree(project_root / "dist", ignore_errors=True)
        rmtree(project_root / "rust" / "target", ignore_errors=True)
        rmtree(project_root / "target", ignore_errors=True)
        for artifact in package_dir.glob("cargo_controls_extension*"):
            if artifact.suffix in (".dll", ".dylib", ".pyd", ".so"):
                artifact.unlink()
        modules.pop("cargo_controls_project", None)
        modules.pop("cargo_controls_project.cargo_controls_extension", None)

        stale_artifact = project_root / "target" / resolve_target_triple() / "release" / shared_library_name("cargo_controls_extension")
        stale_artifact.parent.mkdir(parents=True, exist_ok=True)
        stale_artifact.write_bytes(b"stale artifact")

        # compile
        check_call(
            [
                executable,
                "-m",
                "hatchling",
                "build",
                "--hooks-only",
            ],
            cwd=project_root,
            env=_subprocess_env(),
        )

        # assert built from the isolated target-dir, not the stale project target
        assert stale_artifact.read_bytes() == b"stale artifact"
        assert not (project_root / "rust" / "target").exists()
        if platform == "win32":
            extension_path = package_dir / "cargo_controls_extension.pyd"
        else:
            extension_path = package_dir / "cargo_controls_extension.so"
        assert extension_path.exists()
        assert extension_path.read_bytes() != b"stale artifact"

        # dist
        check_call(
            [
                executable,
                "-m",
                "build",
                "-w",
                "-n",
            ],
            cwd=project_root,
            env=_subprocess_env(),
        )

        assert f"cp3{version_info.minor}-cp3{version_info.minor}" in listdir(project_root / "dist")[0]

        # import
        here = Path(__file__).parent / project_folder
        path.insert(0, str(here))
        cargo_controls_project = import_module("cargo_controls_project")

        assert cargo_controls_project.hello() == "Cargo controls 1"
        assert cargo_controls_project.compile_env() == "enabled"
        assert cargo_controls_project.feature_enabled()

    def test_python_extension_and_c_abi_shared_library_artifacts(self):
        project_folder = "test_project_python_extension_c_abi_library"
        project_root = Path("hatch_rs/tests") / project_folder
        package_dir = project_root / "c_abi_bundle_project"

        # cleanup
        rmtree(project_root / "dist", ignore_errors=True)
        rmtree(project_root / "target", ignore_errors=True)
        rmtree(project_root / "rust" / "target", ignore_errors=True)
        rmtree(package_dir / "lib", ignore_errors=True)
        for artifact in package_dir.glob("c_abi_bundle_extension*"):
            if artifact.suffix in (".dll", ".dylib", ".pyd", ".so"):
                artifact.unlink()
        modules.pop("c_abi_bundle_project", None)

        # compile
        check_call(
            [
                executable,
                "-m",
                "hatchling",
                "build",
                "--hooks-only",
            ],
            cwd=project_root,
            env=_subprocess_env(),
        )

        extension_name = "c_abi_bundle_extension.pyd" if platform == "win32" else "c_abi_bundle_extension.so"
        shared_library = shared_library_name("c_abi_library")
        manifest_path = package_dir / "lib" / "hatch-rs-artifacts.json"
        assert (package_dir / extension_name).exists()
        assert (package_dir / "lib" / shared_library).exists()
        assert (package_dir / "include" / "c_abi_library.h").exists()
        assert manifest_path.exists()
        assert not (project_root / "target").exists()
        assert not (project_root / "rust" / "target").exists()
        if platform == "win32":
            assert list((package_dir / "lib").glob("*.lib")) or list((package_dir / "lib").glob("*.dll.a"))

        # dist
        check_call(
            [
                executable,
                "-m",
                "build",
                "-w",
                "-n",
            ],
            cwd=project_root,
            env=_subprocess_env(),
        )

        wheel_path = next((project_root / "dist").glob("*.whl"))
        with ZipFile(wheel_path) as wheel:
            wheel_names = set(wheel.namelist())
            artifact_manifest = loads(wheel.read("c_abi_bundle_project/lib/hatch-rs-artifacts.json"))
        assert f"c_abi_bundle_project/{extension_name}" in wheel_names
        assert f"c_abi_bundle_project/lib/{shared_library}" in wheel_names
        assert "c_abi_bundle_project/include/c_abi_library.h" in wheel_names
        assert "c_abi_bundle_project/lib/hatch-rs-artifacts.json" in wheel_names
        c_abi_records = [record for record in artifact_manifest["artifacts"] if record["name"] == "c_abi_library"]
        assert any(record["role"] == "cdylib" for record in c_abi_records)
        assert any(record["destination"] == f"c_abi_bundle_project/lib/{shared_library}" for record in c_abi_records)
        if platform == "win32":
            assert any(record["role"] == "import-library" for record in c_abi_records)

        # import
        here = Path(__file__).parent / project_folder
        path.insert(0, str(here))
        c_abi_bundle_project = import_module("c_abi_bundle_project")

        assert c_abi_bundle_project.extension_answer() == 42
        assert c_abi_bundle_project.shared_answer() == 7

    def test_generated_file_and_header_artifacts(self):
        project_folder = "test_project_generated_files"
        project_root = Path("hatch_rs/tests") / project_folder
        package_dir = project_root / "generated_files_project"

        # cleanup
        rmtree(project_root / "dist", ignore_errors=True)
        rmtree(project_root / "build", ignore_errors=True)
        rmtree(package_dir / "generated", ignore_errors=True)
        rmtree(package_dir / "include", ignore_errors=True)
        modules.pop("generated_files_project", None)

        # generate files
        check_call(
            [
                executable,
                "-m",
                "hatchling",
                "build",
                "--hooks-only",
            ],
            cwd=project_root,
            env=_subprocess_env(),
        )

        assert (package_dir / "generated" / "package.txt").read_text() == "generated package data\n"
        assert (project_root / "build" / "generated" / "validated.txt").exists()
        assert "generated_files_project_answer" in (package_dir / "include" / "generated_files_project.h").read_text()

        # dist
        check_call(
            [
                executable,
                "-m",
                "build",
                "-w",
                "-n",
            ],
            cwd=project_root,
            env=_subprocess_env(),
        )

        wheel_path = next((project_root / "dist").glob("*.whl"))
        with ZipFile(wheel_path) as wheel:
            wheel_names = set(wheel.namelist())
        assert "generated_files_project/generated/package.txt" in wheel_names
        assert not any(name.endswith("build/generated/validated.txt") for name in wheel_names)
        assert any(name.endswith(".data/data/include/generated_files_project/generated_files_project.h") for name in wheel_names)

        # import
        here = Path(__file__).parent / project_folder
        path.insert(0, str(here))
        generated_files_project = import_module("generated_files_project")

        assert generated_files_project.generated_text() == "generated package data"

    def test_c_abi_symbol_validation_failure(self):
        project_folder = "test_project_c_abi_validation_failure"
        project_root = Path("hatch_rs/tests") / project_folder
        package_dir = project_root / "validation_failure_project"

        # cleanup
        rmtree(project_root / "dist", ignore_errors=True)
        rmtree(project_root / "target", ignore_errors=True)
        rmtree(package_dir / "lib", ignore_errors=True)

        completed = run(
            [
                executable,
                "-m",
                "hatchling",
                "build",
                "--hooks-only",
            ],
            cwd=project_root,
            env=_subprocess_env(),
            stderr=STDOUT,
            stdout=PIPE,
            text=True,
            check=False,
        )

        assert completed.returncode != 0
        assert "validation_failure_missing_symbol" in completed.stdout

    def test_editable_debug(self):
        project_folder = "test_project_editable_debug"
        # cleanup
        rmtree(f"hatch_rs/tests/{project_folder}/target", ignore_errors=True)
        rmtree(f"hatch_rs/tests/{project_folder}/project/extension.abi3.so", ignore_errors=True)
        rmtree(f"hatch_rs/tests/{project_folder}/project/extension.abi3.pyd", ignore_errors=True)
        modules.pop("project", None)
        modules.pop("project.extension", None)

        # install project in development mode (editable install)
        check_call(
            [
                executable,
                "-m",
                "pip",
                "install",
                "--verbose",
                # ensure hatch-rs from this repository is used and not fetched from PyPI
                "--no-build-isolation",
                "--editable",
                ".",
            ],
            cwd=f"hatch_rs/tests/{project_folder}",
            env=_subprocess_env(),
        )

        # check editable-debug option was honored
        assert Path(f"hatch_rs/tests/{project_folder}/target/debug").exists()
        assert not Path(f"hatch_rs/tests/{project_folder}/target/release").exists()

        # import
        here = Path(__file__).parent / project_folder
        path.insert(0, str(here))
        extension = import_module("project.extension")

        assert extension.hello() == "A string"
