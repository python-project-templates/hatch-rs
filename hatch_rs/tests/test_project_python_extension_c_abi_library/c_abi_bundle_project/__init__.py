from __future__ import annotations

import os
from ctypes import CDLL, c_int
from pathlib import Path
from sys import platform

_PACKAGE_DIR = Path(__file__).resolve().parent
_LIBRARY_DIR = _PACKAGE_DIR / "lib"
_EXTENSION_SUFFIX = ".pyd" if platform == "win32" else ".so"
_SHARED_LIBRARY_NAME = "c_abi_library.dll" if platform == "win32" else "libc_abi_library.dylib" if platform == "darwin" else "libc_abi_library.so"

if platform == "win32" and _LIBRARY_DIR.is_dir():
    os.add_dll_directory(str(_LIBRARY_DIR))

_EXTENSION = CDLL(str(_PACKAGE_DIR / f"c_abi_bundle_extension{_EXTENSION_SUFFIX}"))
_EXTENSION.c_abi_bundle_extension_answer.restype = c_int
_EXTENSION.c_abi_bundle_extension_library_answer.restype = c_int


def extension_answer() -> int:
    return int(_EXTENSION.c_abi_bundle_extension_answer())


def library_answer() -> int:
    """The bundled library's answer, reached through the extension that links it."""
    return int(_EXTENSION.c_abi_bundle_extension_library_answer())


def shared_answer() -> int:
    """The bundled library's answer, loaded directly from the package's lib directory."""
    library = CDLL(str(_LIBRARY_DIR / _SHARED_LIBRARY_NAME))
    library.c_abi_library_answer.restype = c_int
    return int(library.c_abi_library_answer())
