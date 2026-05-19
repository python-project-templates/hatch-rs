from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
package_dir = root / "generated_files_project"
generated_dir = package_dir / "generated"
include_dir = package_dir / "include"
validation_dir = root / "build" / "generated"

generated_dir.mkdir(parents=True, exist_ok=True)
include_dir.mkdir(parents=True, exist_ok=True)
validation_dir.mkdir(parents=True, exist_ok=True)

(generated_dir / "package.txt").write_text("generated package data\n")
(validation_dir / "validated.txt").write_text("validated but not packaged\n")
(include_dir / "generated_files_project.h").write_text(
    "#ifndef GENERATED_FILES_PROJECT_H\n#define GENERATED_FILES_PROJECT_H\nint generated_files_project_answer(void);\n#endif\n"
)
