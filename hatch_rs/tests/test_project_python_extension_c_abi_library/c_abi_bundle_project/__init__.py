from __future__ import annotations

from ctypes import CDLL, c_int
from pathlib import Path
from sys import platform

_PACKAGE_DIR = Path(__file__).parent
_EXTENSION_SUFFIX = ".pyd" if platform == "win32" else ".so"
_SHARED_LIBRARY_NAME = "c_abi_library.dll" if platform == "win32" else "libc_abi_library.dylib" if platform == "darwin" else "libc_abi_library.so"

_EXTENSION = CDLL(str(_PACKAGE_DIR / f"c_abi_bundle_extension{_EXTENSION_SUFFIX}"))
_SHARED_LIBRARY = CDLL(str(_PACKAGE_DIR / "lib" / _SHARED_LIBRARY_NAME))

_EXTENSION.c_abi_bundle_extension_answer.restype = c_int
_SHARED_LIBRARY.c_abi_library_answer.restype = c_int


def extension_answer() -> int:
    return int(_EXTENSION.c_abi_bundle_extension_answer())


def shared_answer() -> int:
    return int(_SHARED_LIBRARY.c_abi_library_answer())
