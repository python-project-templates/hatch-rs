from hatch_rs.plugin import HatchRustBuildHook


def test_custom_build_plan_generate_keeps_no_argument_contract(monkeypatch, tmp_path):
    generated = []

    class CustomBuildPlan:
        def __init__(self, **_config):
            self.commands = []
            self.copied_artifacts = []
            self.shared_data = {"source": "destination"}
            self.shared_scripts = {}
            self.libraries = []

        def generate(self):
            generated.append(True)

        def execute(self):
            pass

        def cleanup(self):
            pass

    class Metadata:
        def __init__(self):
            self.config = {"project": {"name": "project"}}

    monkeypatch.setattr("hatch_rs.plugin.import_string", lambda path: CustomBuildPlan)
    hook = HatchRustBuildHook(
        root=str(tmp_path),
        config={"module": "project", "path": str(tmp_path), "build-plan-class": "tests.CustomBuildPlan"},
        build_config=None,
        metadata=Metadata(),
        directory=str(tmp_path),
        target_name="wheel",
    )

    hook.initialize("standard", {})

    assert generated == [True]
