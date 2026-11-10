import _ctypes

import pytest

from hatch_rs.symbols import exported_symbols


def test_host_extension_module_exports_init_symbol():
    assert "PyInit__ctypes" in exported_symbols(_ctypes.__file__)


def test_wasm_export_section(tmp_path):
    # (module (func $f (export "demo_answer") (result i32) i32.const 42) (memory (export "memory") 1))
    module = bytes.fromhex(
        "0061736d01000000"  # magic, version
        "010501600001 7f"  # type section: () -> i32
        "03020100"  # function section: one function of type 0
        "0503010001"  # memory section: min 1 page
        "071802 0b64656d6f5f616e737765720000 066d656d6f72790200"  # export section: demo_answer (func 0), memory (mem 0)
        "0a06010400412a0b"  # code section
    )
    wasm = tmp_path / "demo.wasm"
    wasm.write_bytes(module)
    assert exported_symbols(wasm) == {"demo_answer", "memory"}


def test_unknown_format_fails_clearly(tmp_path):
    other = tmp_path / "other.so"
    other.write_bytes(b"not a library")
    with pytest.raises(ValueError, match="Unrecognized shared library format"):
        exported_symbols(other)
