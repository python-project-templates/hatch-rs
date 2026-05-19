from __future__ import annotations

from ctypes import CDLL, c_int
from pathlib import Path
from sys import platform

_LIBRARY_SUFFIX = ".pyd" if platform == "win32" else ".so"
_LIBRARY = CDLL(str(Path(__file__).with_name(f"cargo_controls_extension{_LIBRARY_SUFFIX}")))

_LIBRARY.cargo_controls_answer.restype = c_int
_LIBRARY.cargo_controls_env_enabled.restype = c_int
_LIBRARY.cargo_controls_feature_enabled.restype = c_int


def hello():
    return f"Cargo controls {_LIBRARY.cargo_controls_answer()}"


def compile_env():
    return "enabled" if _LIBRARY.cargo_controls_env_enabled() else "missing"


def feature_enabled():
    return bool(_LIBRARY.cargo_controls_feature_enabled())
