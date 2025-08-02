from os import listdir
from pathlib import Path
from shutil import rmtree
from subprocess import check_call
from sys import modules, path, platform

import pytest


class TestProject:
    @pytest.mark.parametrize(
        "project",
        [
            "test_project_basic",
        ],
    )
    def test_basic(self, project):
        # cleanup
        rmtree(f"hatch_rs/tests/{project}/project/extension.so", ignore_errors=True)
        rmtree(f"hatch_rs/tests/{project}/project/extension.pyd", ignore_errors=True)
        modules.pop("project", None)
        modules.pop("project.extension", None)

        # compile
        check_call(
            [
                "hatchling",
                "build",
                "--hooks-only",
            ],
            cwd=f"hatch_rs/tests/{project}",
        )

        # assert built

        if project == "test_project_limited_api" and platform != "win32":
            assert "project.abi3.so" in listdir(f"hatch_rs/tests/{project}/project")
        else:
            if platform == "win32":
                assert "project.pyd" in listdir(f"hatch_rs/tests/{project}/project")
            else:
                assert "project.so" in listdir(f"hatch_rs/tests/{project}/project")

        # import
        here = Path(__file__).parent / project
        path.insert(0, str(here))
        import project.project

        assert project.project.hello() == "A string"
