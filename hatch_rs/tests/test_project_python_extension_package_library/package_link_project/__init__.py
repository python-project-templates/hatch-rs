from __future__ import annotations

from ctypes import CDLL, c_int
from pathlib import Path
from sys import platform

# The producer package adds its lib/ directory to the DLL search path on Windows and,
# elsewhere, loads the library this extension links by its SONAME or install name.
import c_abi_bundle_project  # ty: ignore[import-not-found]

_PACKAGE_DIR = Path(__file__).resolve().parent
_EXTENSION_SUFFIX = ".pyd" if platform == "win32" else ".so"

_EXTENSION = CDLL(str(_PACKAGE_DIR / f"package_link_extension{_EXTENSION_SUFFIX}"))
_EXTENSION.package_link_extension_library_answer.restype = c_int


def library_answer() -> int:
    """The other package's library answer, reached through this extension."""
    return int(_EXTENSION.package_link_extension_library_answer())
