from __future__ import annotations

from pathlib import Path


def generated_text() -> str:
    return (Path(__file__).parent / "generated" / "package.txt").read_text().strip()
