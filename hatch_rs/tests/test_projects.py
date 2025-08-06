from os import listdir
from pathlib import Path
from shutil import rmtree
from subprocess import check_call
from sys import executable, modules, path, platform, version_info

import pytest


class TestProject:
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
                "hatchling",
                "build",
                "--hooks-only",
            ],
            cwd=f"hatch_rs/tests/{project_folder}",
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
        )

        assert f"cp3{version_info.minor}-abi3" in listdir(f"hatch_rs/tests/{project_folder}/dist")[0]

        # import
        here = Path(__file__).parent / project_folder
        path.insert(0, str(here))
        import project.project

        assert project.project.hello() == "A string"
