#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prove the checked /init archive is actually linked into this ARM64 Image."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
from scripts.make_initramfs import parse_newc
from scripts.validate import validate_image, validate_init_elf, validate_initramfs

REQUIRED_SYMBOLS = {"_text", "_end", "__initramfs_start", "__initramfs_size",
                    "on5xelte_stage0_fb_prepare", "on5xelte_stage0_fb_register"}


def kernel_symbols(path: Path) -> dict:
    output = subprocess.check_output(["aarch64-linux-gnu-nm", "--defined-only", str(path)], text=True)
    result = {}
    for line in output.splitlines():
        found = re.fullmatch(r"([0-9a-fA-F]{1,16})\s+[A-Za-z]\s+(\S+)", line.strip())
        if found and found[2] in REQUIRED_SYMBOLS:
            if found[2] in result:
                raise ValueError("duplicate required kernel symbol")
            result[found[2]] = int(found[1], 16)
    if set(result) != REQUIRED_SYMBOLS:
        raise ValueError("built-in archive/native handoff kernel symbols are missing")
    return result


def elf_bytes_at(elf: bytes, address: int, size: int) -> bytes:
    validate_init_elf(elf)  # Shared static ELF64/AArch64 and PT_LOAD bounds contract.
    if (type(address) is not int or type(size) is not int or address < 0 or
            size <= 0 or address + size > 2**64):
        raise ValueError("invalid kernel symbol byte range")
    phoff, = struct.unpack_from("<Q", elf, 32)
    phnum, = struct.unpack_from("<H", elf, 56)
    matches = []
    for index in range(phnum):
        kind, _, offset, virtual, _, filesz, _, _ = struct.unpack_from("<IIQQQQQQ", elf, phoff + 56 * index)
        if kind == 1 and virtual <= address and address + size <= virtual + filesz:
            start = offset + address - virtual
            matches.append(elf[start:start + size])
    if len(matches) != 1 or not size or len(matches[0]) != size:
        raise ValueError("kernel symbol range is not uniquely file-backed in vmlinux")
    return matches[0]


def validate_builtin(image: bytes, vmlinux_path: Path, archive: bytes, init: bytes) -> dict:
    image_report = validate_image(image, load_address=0x40000000)
    init_report = validate_initramfs(archive, built_in=True)
    validate_init_elf(init)
    entries = parse_newc(gzip.decompress(archive))
    if entries["init"]["data"] != init or b"MAINLINE_STAGE0_USERSPACE_OK" not in init:
        raise ValueError("built-in /init differs from the checked diagnostic executable")
    symbols = kernel_symbols(vmlinux_path)
    text = symbols["_text"]
    for name in ("on5xelte_stage0_fb_prepare", "on5xelte_stage0_fb_register"):
        if not text + 64 <= symbols[name] < text + len(image):
            raise ValueError("native handoff symbol is outside the linked Image")
    if symbols["_end"] - text != image_report["required_memory_bytes"]:
        raise ValueError("Image memory requirement differs from vmlinux _text/_end")
    offset = symbols["__initramfs_start"] - text
    size_offset = symbols["__initramfs_size"] - text
    if offset < 64 or size_offset < 64 or size_offset + 8 > len(image):
        raise ValueError("built-in initramfs symbols outside Image file bounds")
    size, = struct.unpack_from("<Q", image, size_offset)
    if size != len(archive) or offset + size > len(image) or image[offset:offset + size] != archive:
        raise ValueError("checked initramfs bytes are not linked at their Image symbol bounds")
    elf = vmlinux_path.read_bytes()
    if (elf_bytes_at(elf, symbols["__initramfs_start"], size) != archive or
            elf_bytes_at(elf, symbols["__initramfs_size"], 8) != struct.pack("<Q", size)):
        raise ValueError("vmlinux and Image built-in archive disagree")
    return {"built_in_initramfs_verified": True, "native_handoff_symbols_present": True,
            "image_archive_offset": offset, "image_size_symbol_offset": size_offset,
            "linked_archive_bytes": size, "archive_sha256": hashlib.sha256(archive).hexdigest(),
            "init_sha256": hashlib.sha256(init).hexdigest(), "image": image_report,
            "initramfs": init_report, "userspace_delivery": "built-in, independently of external ramdisk",
            "hardware_boot_tested": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("image", "vmlinux", "initramfs", "init"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = validate_builtin(args.image.read_bytes(), args.vmlinux,
                              args.initramfs.read_bytes(), args.init.read_bytes())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
