from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest
from hatchling.metadata.core import ProjectMetadata

from hatch_rs import structs
from hatch_rs.plugin import HatchRustBuildHook
from hatch_rs.structs import HatchRustBuildPlan

BUNDLE: dict[str, Any] = {
    "root_name": "project",
    "third_party_libraries": [
        {
            "package_name": "dependency",
            "package_version": "1.0.0",
            "repository": "https://example.com/dependency",
            "license": "MIT",
            "licenses": [{"license": "MIT", "text": "License text including the author's copyright notice\n"}],
        }
    ],
}


@pytest.fixture
def cargo_runner(monkeypatch, tmp_path):
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SKIP_HATCH_RUST", raising=False)
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)

    def run(args, *, cwd, env, check, **kwargs):
        calls.append((tuple(args), Path(cwd), {key: env[key] for key in ("CARGO_TARGET_DIR", "COLLECTOR_CONTEXT") if key in env}))
        if args[:2] == ("cargo", "rustc") or args[:2] == ["cargo", "rustc"]:
            target = args[args.index("--target") + 1]
            target_dir = Path(env.get("CARGO_TARGET_DIR", Path(cwd) / "target"))
            artifact = "project"
            if "--package" in args:
                artifact = args[args.index("--package") + 1]
            output = target_dir / target / "release" / f"lib{artifact}.so"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"compiled library")
        elif args[:2] == ["cargo", "metadata"]:
            return CompletedProcess(
                args,
                0,
                stdout=dumps(
                    {
                        "packages": [
                            {
                                "name": name,
                                "id": name,
                                "manifest_path": str(Path(cwd) / "Cargo.toml"),
                                "features": {"default": [], "extra": []},
                            }
                            for name in ("project", "second")
                        ]
                    }
                ),
            )
        elif args[:2] == ["cargo", "pkgid"]:
            return CompletedProcess(args, 0, stdout=args[args.index("--package") + 1] + "\n")
        elif args[0] == "cargo-bundle-licenses":
            output = Path(args[args.index("--output") + 1])
            output.write_text(dumps(BUNDLE), encoding="utf-8")
        else:
            raise AssertionError(f"Unexpected command: {args}")
        return CompletedProcess(args, 0)

    monkeypatch.setattr("hatch_rs.structs.run", run)
    return calls


def make_plan(tmp_path, **config):
    rust = tmp_path / "rust project"
    rust.mkdir(exist_ok=True)
    (rust / "Cargo.toml").write_text('[package]\nname = "project"\nversion = "0.1.0"\n')
    options: dict[str, Any] = {"target-dir": "isolated", **config}
    return HatchRustBuildPlan(
        module="project",
        path=rust,
        target="x86_64-unknown-linux-gnu",
        **options,
    )


def test_license_collection_is_opt_in(tmp_path, cargo_runner):
    plan = make_plan(tmp_path)
    plan.execute()
    plan.cleanup()

    assert len(cargo_runner) == 1
    assert (tmp_path / "project/project.so").read_bytes() == b"compiled library"
    assert not (tmp_path / "project/licenses").exists()


def test_license_bundle_survives_cleanup_and_is_registered_with_hook(tmp_path, cargo_runner):
    rust = tmp_path / "rust project"
    rust.mkdir()
    (rust / "Cargo.toml").write_text('[package]\nname = "project"\nversion = "0.1.0"\n')
    hook = HatchRustBuildHook(
        root=str(tmp_path),
        config={"module": "project", "path": str(rust), "target": "x86_64-unknown-linux-gnu", "target-dir": "isolated", "licenses": {}},
        build_config=None,
        metadata=ProjectMetadata(str(tmp_path), None, {"project": {"name": "project", "version": "0.1.0"}}),
        directory=str(tmp_path),
        target_name="wheel",
    )
    build_data = {}

    hook.initialize("standard", build_data)

    assert loads((tmp_path / "project/licenses/cargo.json").read_text()) == BUNDLE
    assert build_data["force_include"]["project/licenses/cargo.json"] == "project/licenses/cargo.json"
    collector = cargo_runner[-1]
    assert collector[0][:3] == ("cargo-bundle-licenses", "--format", "json")
    assert collector[1] == rust
    assert not Path(collector[0][collector[0].index("--output") + 1]).exists()


def test_license_collection_uses_manifest_directory_and_artifact_features(tmp_path, cargo_runner):
    previous = tmp_path / "reviewed.json"
    previous.write_text(dumps(BUNDLE))
    plan = make_plan(
        tmp_path,
        manifest="Cargo.toml",
        features=["ignored-global-feature"],
        env={"COLLECTOR_CONTEXT": "global"},
        licenses={"previous": "../reviewed.json", "check-previous": True},
        artifacts=[
            {
                "name": "project",
                "package": "project",
                "destination": "project/lib/{shared_library}",
                "features": ["extra"],
                "env": {"COLLECTOR_CONTEXT": "artifact"},
            }
        ],
    )
    plan.execute()
    plan.cleanup()

    args, cwd, env = cargo_runner[-1]
    assert cwd == tmp_path / "rust project"
    assert args[args.index("--features") + 1] == "project/extra"
    assert args[args.index("--previous") + 1] == previous.as_posix()
    assert "--check-previous" in args
    assert env["COLLECTOR_CONTEXT"] == "artifact"
    assert loads((tmp_path / "project/licenses/project.json").read_text()) == BUNDLE


def test_all_features_are_expanded_for_the_license_collector(tmp_path, cargo_runner):
    plan = make_plan(tmp_path, **{"all-features": True, "licenses": {}})
    plan.execute()
    plan.cleanup()

    args = cargo_runner[-1][0]
    assert set(args[args.index("--features") + 1].split(",")) == {"project/default", "project/extra"}
    assert any(call[0][:2] == ("cargo", "metadata") for call in cargo_runner)


def test_separate_license_bundles_for_multiple_artifacts(tmp_path, cargo_runner):
    plan = make_plan(
        tmp_path,
        licenses={},
        artifacts=[{"name": name, "package": name, "destination": "project/lib/{shared_library}"} for name in ("project", "second")],
    )
    plan.execute()
    plan.cleanup()

    assert loads((tmp_path / "project/licenses/project.json").read_text()) == BUNDLE
    assert loads((tmp_path / "project/licenses/second.json").read_text()) == BUNDLE
    assert len([call for call in cargo_runner if call[0][0] == "cargo-bundle-licenses"]) == 2


def test_generated_and_skipped_artifacts_do_not_collect_licenses(tmp_path, cargo_runner, monkeypatch):
    monkeypatch.setenv("SKIP_SECOND", "1")
    rust = tmp_path / "rust project"
    rust.mkdir()
    (rust / "generated.txt").write_text("generated\n")
    plan = make_plan(
        tmp_path,
        licenses={},
        artifacts=[
            {"name": "header", "outputs": [{"source": "generated.txt", "destination": "project/header.txt"}]},
            {"name": "second", "skip-if-env": "SKIP_SECOND"},
        ],
    )
    plan.execute()
    plan.cleanup()

    assert cargo_runner == []
    assert (tmp_path / "project/header.txt").read_text() == "generated\n"


@pytest.mark.parametrize("text", ["NOT FOUND", "", "   "])
def test_missing_license_text_fails_the_build(tmp_path, cargo_runner, monkeypatch, text):
    monkeypatch.setitem(BUNDLE["third_party_libraries"][0]["licenses"][0], "text", text)
    plan = make_plan(tmp_path, licenses={})

    try:
        with pytest.raises(RuntimeError, match="dependency.*license text"):
            plan.execute()
    finally:
        plan.cleanup()
    assert not (tmp_path / "project/licenses/cargo.json").exists()


@pytest.mark.parametrize("failure", ["missing-tool", "command-failed", "missing-output", "invalid-json"])
def test_collector_failures_are_not_silently_packaged(tmp_path, cargo_runner, monkeypatch, failure):
    original_run = structs.run

    def failing_run(args, **kwargs):
        if args[0] != "cargo-bundle-licenses":
            return original_run(args, **kwargs)
        if failure == "missing-tool":
            raise FileNotFoundError("cargo-bundle-licenses")
        if failure == "invalid-json":
            Path(args[args.index("--output") + 1]).write_text("not JSON")
        return CompletedProcess(args, 1 if failure == "command-failed" else 0)

    monkeypatch.setattr("hatch_rs.structs.run", failing_run)
    plan = make_plan(tmp_path, licenses={})

    try:
        with pytest.raises(RuntimeError, match="license|License"):
            plan.execute()
    finally:
        plan.cleanup()


@pytest.mark.parametrize("destination", ["../outside.json", "/outside.json", "", "project/project.so"])
def test_license_destination_cannot_escape_or_overwrite_an_artifact(tmp_path, cargo_runner, destination):
    plan = make_plan(tmp_path, licenses={"destination": destination})

    try:
        with pytest.raises(ValueError, match="destination"):
            plan.execute()
    finally:
        plan.cleanup()


def test_check_previous_requires_a_reviewed_bundle(tmp_path):
    with pytest.raises(ValueError, match="previous"):
        make_plan(tmp_path, licenses={"check-previous": True})


def test_check_previous_rejects_unreviewed_license_additions(tmp_path, cargo_runner, monkeypatch):
    previous = tmp_path / "reviewed.json"
    previous.write_text(dumps(BUNDLE))
    monkeypatch.setitem(
        BUNDLE["third_party_libraries"][0],
        "licenses",
        [*BUNDLE["third_party_libraries"][0]["licenses"], {"license": "Zlib", "text": "New, unreviewed license text"}],
    )
    plan = make_plan(tmp_path, licenses={"previous": "../reviewed.json", "check-previous": True})

    try:
        with pytest.raises(RuntimeError, match="reviewed"):
            plan.execute()
    finally:
        plan.cleanup()


def test_existing_directory_is_not_a_license_file_destination(tmp_path, cargo_runner):
    destination = tmp_path / "project/licenses"
    destination.mkdir(parents=True)
    (destination / "unrelated.txt").write_text("do not package this file")
    plan = make_plan(tmp_path, licenses={"destination": "project/licenses"})

    try:
        with pytest.raises(ValueError, match="destination"):
            plan.execute()
    finally:
        plan.cleanup()


@pytest.mark.parametrize("writer", ["generated-output", "artifact-manifest"])
def test_license_bundle_cannot_be_overwritten_by_later_writers(tmp_path, cargo_runner, writer):
    destination = "project/licenses/project.json"
    artifacts: list[dict[str, Any]] = [{"name": "project", "destination": "project/lib/{shared_library}"}]
    config: dict[str, Any] = {
        "licenses": {},
        "artifacts": artifacts,
    }
    if writer == "generated-output":
        rust = tmp_path / "rust project"
        rust.mkdir()
        (rust / "generated.txt").write_text("not a license bundle")
        config["artifacts"].append({"name": "generated", "outputs": [{"source": "generated.txt", "destination": destination}]})
    else:
        config.update({"artifact-manifest": True, "artifact-manifest-destination": destination})
    plan = make_plan(tmp_path, **config)

    try:
        with pytest.raises(ValueError, match="destination"):
            plan.execute()
    finally:
        plan.cleanup()


@pytest.mark.parametrize(
    "cargo_args",
    [
        ["--features", "extra"],
        ["--features=extra"],
        ["-Fextra"],
        ["-vFextra"],
        ["-qvFextra"],
        ["-rFextra"],
        ["-hFextra"],
        ["--all-features"],
        ["--package=second"],
        ["-psecond"],
        ["-vpsecond"],
        ["-rpsecond"],
        ["--manifest-path", "other/Cargo.toml"],
        ["--target=wasm32-unknown-unknown"],
    ],
)
def test_license_collection_rejects_raw_dependency_selection_overrides(tmp_path, cargo_runner, cargo_args):
    plan = make_plan(tmp_path, licenses={}, **{"cargo-args": cargo_args})

    try:
        with pytest.raises(ValueError, match="cargo-args.*structured"):
            plan.execute()
    finally:
        plan.cleanup()
