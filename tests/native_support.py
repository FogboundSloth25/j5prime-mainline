# SPDX-License-Identifier: GPL-2.0-only
"""Independently written native-format fixtures, never firmware or phone input."""
import struct
from tests.support import synthetic_elf, synthetic_image
from scripts.make_initramfs import build_newc, compress_newc


def synthetic_fdt() -> bytearray:
    strings = b"compatible\0"
    value = b"synthetic,fixture\0"
    structure = struct.pack(">I", 1) + b"\0" * 4
    structure += struct.pack(">III", 3, len(value), 0) + value
    structure += b"\0" * (-len(structure) % 4)
    structure += struct.pack(">II", 2, 9)
    total = 56 + len(structure) + len(strings)
    return bytearray(struct.pack(">10I", 0xD00DFEED, total, 56, 56 + len(structure),
                                 40, 17, 16, 0, len(strings), len(structure))
                     + b"\0" * 16 + structure + strings)


def linked_fixture(marker: bytes = b"MAINLINE_STAGE0_USERSPACE_OK") -> tuple:
    init = synthetic_elf()
    init[300:300 + len(marker)] = marker
    archive = compress_newc(build_newc(bytes(init)))
    image = synthetic_image()
    image.extend(b"\0" * (4096 - len(image)))
    struct.pack_into("<Q", image, 16, 8192)
    image[512:512 + len(archive)] = archive
    struct.pack_into("<Q", image, 3840, len(archive))
    text = 0xFFFF800080000000
    symbols = {"_text": text, "_end": text + 8192,
               "__initramfs_start": text + 512, "__initramfs_size": text + 3840,
               "on5xelte_stage0_fb_prepare": text + 64,
               "on5xelte_stage0_fb_register": text + 128}
    elf = bytearray(256 + len(image))
    elf[:120] = synthetic_elf()[:120]
    struct.pack_into("<Q", elf, 24, text)
    struct.pack_into("<IIQQQQQQ", elf, 64,
                     1, 5, 256, text, text, len(image), 8192, 256)
    elf[256:] = image
    return image, elf, archive, bytes(init), symbols
