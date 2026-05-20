from __future__ import annotations

from ctypes import CDLL, c_int, c_uint
from pathlib import Path
from sys import argv

library_path = Path(argv[1])
header_path = Path(argv[2])

library = CDLL(str(library_path))
library.c_abi_library_answer.restype = c_int
library.c_abi_library_abi_version.restype = c_uint

if library.c_abi_library_answer() != 7:
    raise SystemExit("c_abi_library_answer returned the wrong value")
if library.c_abi_library_abi_version() != 1:
    raise SystemExit("c_abi_library_abi_version returned the wrong value")

header = header_path.read_text()
for expected in ("C_ABI_LIBRARY_ABI_VERSION", "c_abi_library_answer", "c_abi_library_abi_version"):
    if expected not in header:
        raise SystemExit(f"missing expected header text: {expected}")
