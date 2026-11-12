from importlib import import_module
from json import loads
from os import environ, listdir, pathsep
from os.path import dirname
from pathlib import Path
from shutil import copy2, copytree, rmtree, which
from subprocess import PIPE, STDOUT, check_call, run
from sys import executable, modules, path, platform, version_info
from zipfile import ZipFile

import pytest

from hatch_rs.structs import resolve_target_triple, shared_library_name
from hatch_rs.symbols import exported_symbols

REPO_ROOT = Path(__file__).resolve().parents[2]
C_ABI_PROJECT = Path("hatch_rs/tests") / "test_project_python_extension_c_abi_library"
C_ABI_PACKAGE = C_ABI_PROJECT / "c_abi_bundle_project"


def _clean_c_abi_project():
    rmtree(C_ABI_PROJECT / "dist", ignore_errors=True)
    rmtree(C_ABI_PROJECT / "target", ignore_errors=True)
    rmtree(C_ABI_PROJECT / "rust" / "target", ignore_errors=True)
    rmtree(C_ABI_PACKAGE / "lib", ignore_errors=True)
    rmtree(C_ABI_PACKAGE / "include", ignore_errors=True)
    for artifact in C_ABI_PACKAGE.glob("c_abi_bundle_extension*"):
        if artifact.suffix in (".dll", ".dylib", ".pyd", ".so"):
            artifact.unlink()


def _assert_c_abi_answers(env, *, dll_dir=None, shared=True):
    """Import the fixture package in a fresh interpreter and call through the linked library."""
    code = f"import os; os.add_dll_directory({str(dll_dir)!r})\n" if dll_dir else ""
    code += "import c_abi_bundle_project as m\n"
    code += "assert m.extension_answer() == 42\nassert m.library_answer() == 7\n"
    if shared:
        code += "assert m.shared_answer() == 7\n"
    check_call([executable, "-c", code], cwd=C_ABI_PROJECT, env=env)


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
    @pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
    def test_cargo_licenses_in_wheel_built_from_sdist(self, tmp_path, workspace, newline):
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
        license_bytes = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8").replace("\n", newline).encode("utf-8")
        for name in ("licensed-dependency", "extra-dependency"):
            dependency = rust / "deps" / name
            (dependency / "src").mkdir(parents=True)
            (dependency / "Cargo.toml").write_text(f'[package]\nname = "{name}"\nversion = "1.0.0"\nedition = "2021"\nlicense = "Apache-2.0"\n')
            (dependency / "src/lib.rs").write_text("pub fn answer() -> u32 { 42 }\n")
            (dependency / "LICENSE").write_bytes(license_bytes)
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
            assert library["licenses"][0]["text"].encode("utf-8") == license_bytes
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
        project_root = C_ABI_PROJECT
        package_dir = C_ABI_PACKAGE
        _clean_c_abi_project()

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
        assert "#define C_ABI_LIBRARY_ABI_VERSION 1" in (package_dir / "include" / "c_abi_library.h").read_text()
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

        # the extension reaches the library through its rpath, not by compiling it in
        assert "c_abi_library_answer" not in exported_symbols(package_dir / extension_name)
        _assert_c_abi_answers(_subprocess_env())

    def test_python_extension_links_external_c_abi_library(self, tmp_path):
        _clean_c_abi_project()
        shared_library = shared_library_name("c_abi_library")

        # build the library into its own prefix, the way a conda package would ship it
        prefix = tmp_path / "prefix"
        (prefix / "lib").mkdir(parents=True)
        (prefix / "bin").mkdir()
        rustc_args = ["--", "-C", "link-arg=-Wl,-install_name,@rpath/libc_abi_library.dylib"] if platform == "darwin" else []
        check_call(
            [
                "cargo",
                "rustc",
                "--manifest-path",
                "rust/Cargo.toml",
                "--release",
                "--target-dir",
                str(tmp_path / "target"),
                "--crate-type",
                "cdylib",
                *rustc_args,
            ],
            cwd=C_ABI_PROJECT,
            env=_subprocess_env(),
        )
        built = tmp_path / "target" / "release"
        if platform == "win32":
            copy2(built / shared_library, prefix / "bin" / shared_library)
            copy2(built / "c_abi_library.dll.lib", prefix / "lib" / "c_abi_library.lib")
        else:
            copy2(built / shared_library, prefix / "lib" / shared_library)

        env = _subprocess_env()
        env.update({"HATCH_RS_LIBRARY_SOURCE": "external", "HATCH_RS_LIBRARY_ROOT": str(prefix)})
        check_call([executable, "-m", "hatchling", "build", "--hooks-only"], cwd=C_ABI_PROJECT, env=env)

        assert not (C_ABI_PACKAGE / "lib" / shared_library).exists()
        records = loads((C_ABI_PACKAGE / "lib" / "hatch-rs-artifacts.json").read_text())["artifacts"]
        external = [record for record in records if record["name"] == "c_abi_library"]
        assert [record["role"] for record in external] == ["external-library"]
        assert Path(external[0]["source"]).is_relative_to(prefix)

        # import with the prefix on the loader path, as an environment would provide it
        if platform == "win32":
            _assert_c_abi_answers(env, dll_dir=prefix / "bin", shared=False)
        else:
            env["DYLD_LIBRARY_PATH" if platform == "darwin" else "LD_LIBRARY_PATH"] = str(prefix / "lib")
            _assert_c_abi_answers(env, shared=False)

    def test_python_extension_links_library_from_installed_package(self, tmp_path):
        """A second package links the library the first one ships, as fsspec-kafka does with fsspec-rs."""
        consumer_root = Path("hatch_rs/tests") / "test_project_python_extension_package_library"
        consumer_package = consumer_root / "package_link_project"
        rmtree(consumer_root / "dist", ignore_errors=True)
        rmtree(consumer_package / "lib", ignore_errors=True)
        for artifact in consumer_package.glob("package_link_extension*"):
            artifact.unlink()
        _clean_c_abi_project()
        check_call([executable, "-m", "hatchling", "build", "--hooks-only"], cwd=C_ABI_PROJECT, env=_subprocess_env())

        # the producer is importable in the build environment, as an installed build dependency would be
        env = _subprocess_env()
        env["PYTHONPATH"] = f"{C_ABI_PROJECT.resolve()}{pathsep}{env['PYTHONPATH']}"
        check_call([executable, "-m", "hatchling", "build", "--hooks-only"], cwd=consumer_root, env=env)

        extension_name = "package_link_extension.pyd" if platform == "win32" else "package_link_extension.so"
        assert (consumer_package / extension_name).exists()
        assert list((consumer_package / "lib").iterdir()) == [consumer_package / "lib" / "hatch-rs-artifacts.json"]
        records = loads((consumer_package / "lib" / "hatch-rs-artifacts.json").read_text())["artifacts"]
        library = [record for record in records if record["name"] == "c_abi_library"]
        assert [record["role"] for record in library] == ["external-library"]
        assert Path(library[0]["source"]).is_relative_to(C_ABI_PACKAGE.resolve())
        assert "c_abi_library_answer" not in exported_symbols(consumer_package / extension_name)

        # install both packages side by side and call through to the producer's library
        site = tmp_path / "site"
        copytree(C_ABI_PACKAGE, site / "c_abi_bundle_project")
        copytree(consumer_package, site / "package_link_project")
        env["PYTHONPATH"] = str(site)
        check_call([executable, "-c", "import package_link_project as m; assert m.library_answer() == 7"], cwd=tmp_path, env=env)
        if platform != "win32":
            # the rpath alone finds the sibling package's library, without the producer being imported first
            code = f"from ctypes import CDLL; assert CDLL({str(site / 'package_link_project' / extension_name)!r}).package_link_extension_library_answer() == 7"
            check_call([executable, "-c", code], cwd=tmp_path, env=env)

    def test_python_extension_compiles_c_abi_library_in_direct_mode(self):
        _clean_c_abi_project()
        env = _subprocess_env()
        env["HATCH_RS_LIBRARY_SOURCE"] = "direct"
        check_call([executable, "-m", "hatchling", "build", "--hooks-only"], cwd=C_ABI_PROJECT, env=env)

        extension_name = "c_abi_bundle_extension.pyd" if platform == "win32" else "c_abi_bundle_extension.so"
        assert not (C_ABI_PACKAGE / "lib" / shared_library_name("c_abi_library")).exists()
        assert "c_abi_library_answer" in exported_symbols(C_ABI_PACKAGE / extension_name)
        _assert_c_abi_answers(env, shared=False)

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

    def test_cbindgen_build_script_header(self):
        project_folder = "test_project_cbindgen_build_script"
        project_root = Path("hatch_rs/tests") / project_folder
        package_dir = project_root / "build_script_project"

        # cleanup
        rmtree(project_root / "dist", ignore_errors=True)
        rmtree(project_root / "target", ignore_errors=True)
        rmtree(package_dir / "lib", ignore_errors=True)
        rmtree(package_dir / "include", ignore_errors=True)
        modules.pop("build_script_project", None)

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

        header = (package_dir / "include" / "build_script_header.h").read_text()
        assert "#define BUILD_SCRIPT_HEADER_ABI_VERSION 1" in header
        assert "build_script_header_answer" in header
        assert (package_dir / "lib" / shared_library_name("build_script_header")).exists()

        # import
        here = Path(__file__).parent / project_folder
        path.insert(0, str(here))
        build_script_project = import_module("build_script_project")

        assert build_script_project.answer() == 9

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

    def test_c_abi_header_symbol_validation_failure(self):
        project_folder = "test_project_c_abi_header_symbols"
        project_root = Path("hatch_rs/tests") / project_folder
        package_dir = project_root / "header_symbols_project"

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
        assert "header_symbols_missing" in completed.stdout
        assert "header_symbols_check" not in completed.stdout

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
