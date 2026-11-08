# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic format fixtures, never kernels or device dumps."""

from pathlib import Path
import struct
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def temporary_directory():
    approved = Path("/tmp/opencode")
    return tempfile.TemporaryDirectory(dir=approved if approved.is_dir() else None)


def synthetic_image() -> bytearray:
    image = bytearray(512)
    # Ordinary NOP at byte 0, B from byte 4 to byte 64.
    struct.pack_into("<IIQQQ", image, 0, 0xD503201F, 0x1400000F, 0, 4096, 0xA)
    struct.pack_into("<I", image, 56, 0x644D5241)
    return image


def synthetic_elf() -> bytearray:
    elf = bytearray(512)
    elf[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<HHIQQQIHHHHHH", elf, 16,
                     2, 183, 1, 0x400100, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    struct.pack_into("<IIQQQQQQ", elf, 64,
                     1, 5, 0, 0x400000, 0x400000, len(elf), 4096, 4096)
    return elf
