"""Read exported symbol names from shared libraries without loading them.

Supports ELF, Mach-O (including fat binaries), PE, and WebAssembly so that
cross-compiled and Pyodide artifacts can be checked on any host.
"""

from __future__ import annotations

from pathlib import Path
from struct import Struct, unpack_from

__all__ = ("exported_symbols",)

_ELF_MAGIC = b"\x7fELF"
_PE_MAGIC = b"MZ"
_WASM_MAGIC = b"\x00asm"
_MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe": ("<", 64),
    b"\xfe\xed\xfa\xcf": (">", 64),
    b"\xce\xfa\xed\xfe": ("<", 32),
    b"\xfe\xed\xfa\xce": (">", 32),
}
_FAT_MAGICS = {b"\xca\xfe\xba\xbe": 32, b"\xca\xfe\xba\xbf": 64}


def exported_symbols(path: Path | str) -> set[str]:
    """Return the names a shared library exports, as a C caller would spell them."""
    data = Path(path).read_bytes()
    if data[:4] == _ELF_MAGIC:
        return _elf_exports(data)
    if data[:4] in _MACHO_MAGICS:
        return _macho_exports(data)
    if data[:4] in _FAT_MAGICS:
        return _fat_exports(data)
    if data[:2] == _PE_MAGIC:
        return _pe_exports(data)
    if data[:4] == _WASM_MAGIC:
        return _wasm_exports(data)
    raise ValueError(f"Unrecognized shared library format: {path}")


def _cstring(data: bytes, offset: int) -> str:
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("utf-8", errors="replace")


def _elf_exports(data: bytes) -> set[str]:
    is_64 = data[4] == 2
    endian = "<" if data[5] == 1 else ">"
    if is_64:
        shoff, shentsize, shnum = unpack_from(f"{endian}Q", data, 0x28)[0], *unpack_from(f"{endian}HH", data, 0x3A)
        section_header = Struct(f"{endian}IIQQQQIIQQ")
        symbol = Struct(f"{endian}IBBHQQ")
    else:
        shoff, shentsize, shnum = unpack_from(f"{endian}I", data, 0x20)[0], *unpack_from(f"{endian}HH", data, 0x2E)
        section_header = Struct(f"{endian}IIIIIIIIII")
        symbol = Struct(f"{endian}IIIBBH")

    sections = [section_header.unpack_from(data, shoff + index * shentsize) for index in range(shnum)]
    dynsym = next((section for section in sections if section[1] == 11), None)
    if dynsym is None:
        raise ValueError("ELF file has no dynamic symbol table")
    _, _, _, _, sym_offset, sym_size, link, _, _, entsize = dynsym
    str_offset = sections[link][4]

    exported = set()
    for index in range(sym_size // (entsize or symbol.size)):
        fields = symbol.unpack_from(data, sym_offset + index * (entsize or symbol.size))
        if is_64:
            name, info, _, shndx = fields[0], fields[1], fields[2], fields[3]
        else:
            name, info, shndx = fields[0], fields[3], fields[5]
        if shndx == 0 or info >> 4 not in (1, 2) or name == 0:
            continue
        exported.add(_cstring(data, str_offset + name))
    return exported


def _macho_exports(data: bytes, base: int = 0) -> set[str]:
    endian, bits = _MACHO_MAGICS[data[base : base + 4]]
    header_size = 32 if bits == 64 else 28
    ncmds = unpack_from(f"{endian}I", data, base + 16)[0]
    nlist = Struct(f"{endian}IBBHQ") if bits == 64 else Struct(f"{endian}IBBhI")

    offset = base + header_size
    symtab = None
    for _ in range(ncmds):
        cmd, cmdsize = unpack_from(f"{endian}II", data, offset)
        if cmd == 0x2:
            symtab = unpack_from(f"{endian}IIII", data, offset + 8)
        offset += cmdsize
    if symtab is None:
        raise ValueError("Mach-O file has no symbol table")
    symoff, nsyms, stroff, _ = symtab

    exported = set()
    for index in range(nsyms):
        strx, n_type, _, _, _ = nlist.unpack_from(data, base + symoff + index * nlist.size)
        if n_type & 0xE0 or not n_type & 0x01 or n_type & 0x0E != 0x0E:
            continue
        name = _cstring(data, base + stroff + strx)
        exported.add(name.removeprefix("_"))
    return exported


def _fat_exports(data: bytes) -> set[str]:
    bits = _FAT_MAGICS[data[:4]]
    narch = unpack_from(">I", data, 4)[0]
    arch = Struct(">IIQQII") if bits == 64 else Struct(">IIIII")
    exported = set()
    for index in range(narch):
        fields = arch.unpack_from(data, 8 + index * arch.size)
        exported |= _macho_exports(data, base=fields[2])
    return exported


def _pe_exports(data: bytes) -> set[str]:
    pe_offset = unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        raise ValueError("PE file has no PE signature")
    nsections, optional_size = unpack_from("<H", data, pe_offset + 6)[0], unpack_from("<H", data, pe_offset + 20)[0]
    optional_offset = pe_offset + 24
    magic = unpack_from("<H", data, optional_offset)[0]
    directory_offset = optional_offset + (112 if magic == 0x20B else 96)
    export_rva, export_size = unpack_from("<II", data, directory_offset)
    if export_rva == 0 or export_size == 0:
        return set()

    section = Struct("<8sIIIIIIHHI")
    sections_offset = optional_offset + optional_size
    sections = [section.unpack_from(data, sections_offset + index * section.size) for index in range(nsections)]

    def file_offset(rva: int) -> int:
        for _, virtual_size, virtual_address, raw_size, raw_pointer, *_ in sections:
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                return rva - virtual_address + raw_pointer
        raise ValueError(f"PE RVA {rva:#x} is not inside any section")

    export_offset = file_offset(export_rva)
    nnames, _, names_rva = unpack_from("<III", data, export_offset + 24)
    names_offset = file_offset(names_rva)
    return {_cstring(data, file_offset(unpack_from("<I", data, names_offset + index * 4)[0])) for index in range(nnames)}


def _leb128(data: bytes, offset: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, offset


def _wasm_exports(data: bytes) -> set[str]:
    offset = 8
    while offset < len(data):
        section_id = data[offset]
        size, offset = _leb128(data, offset + 1)
        if section_id != 7:
            offset += size
            continue
        count, offset = _leb128(data, offset)
        exported = set()
        for _ in range(count):
            length, offset = _leb128(data, offset)
            exported.add(data[offset : offset + length].decode("utf-8", errors="replace"))
            offset += length
            _, offset = _leb128(data, offset + 1)
        return exported
    return set()
