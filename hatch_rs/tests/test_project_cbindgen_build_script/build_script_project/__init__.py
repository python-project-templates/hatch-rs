from __future__ import annotations

from ctypes import CDLL, c_int
from pathlib import Path
from sys import platform

_SHARED_LIBRARY_NAME = (
    "build_script_header.dll" if platform == "win32" else "libbuild_script_header.dylib" if platform == "darwin" else "libbuild_script_header.so"
)
_SHARED_LIBRARY = CDLL(str(Path(__file__).parent / "lib" / _SHARED_LIBRARY_NAME))
_SHARED_LIBRARY.build_script_header_answer.restype = c_int


def answer() -> int:
    return int(_SHARED_LIBRARY.build_script_header_answer())
