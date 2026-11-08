#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fail-closed checks of stage0 build components; no phone access."""

import argparse
import gzip
import io
import json
from pathlib import Path
import re
import stat
import struct
import subprocess

from scripts.make_initramfs import parse_newc

PAYLOAD_ENTRY = 0x50000000
RAMDISK_ENTRY = 0x42000000
TIMA_START = 0x52800000
MAX_INITRAMFS = 16 * 1024 * 1024


def parse_config(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        enabled = re.fullmatch(r"(CONFIG_\w+)=(.*)", line)
        disabled = re.fullmatch(r"# (CONFIG_\w+) is not set", line)
        if enabled:
            key, value = enabled.groups()
        elif disabled:
            key, value = disabled[1], "n"
        else:
            continue
        if key in result and result[key] != value:
            raise ValueError(f"conflicting config values for {key}")
        result[key] = value
    return result


def validate_config(actual_text: str, fragment_text: str) -> dict:
    actual, requested = parse_config(actual_text), parse_config(fragment_text)
    if not requested:
        raise ValueError("requested config fragment is empty")
    errors = []
    for key, wanted in requested.items():
        value = actual.get(key, "n")
        if value != wanted:
            errors.append(f"{key}: requested {wanted}, resolved {value}")
    # These hidden/core symbols must be selected, not merely assumed.
    for key in ("CONFIG_ARM_GIC", "CONFIG_ARM_PSCI_FW", "CONFIG_ARM_ARCH_TIMER",
                "CONFIG_CLKSRC_EXYNOS_MCT", "CONFIG_COMMON_CLK"):
        if actual.get(key) != "y":
            errors.append(f"{key}: required built-in platform support is missing")
    if errors:
        raise ValueError("Kconfig checks failed:\n" + "\n".join(errors))
    return {"requested_options_checked": len(requested), "core_platform_checks": 5}


def validate_image(image: bytes) -> dict:
    if len(image) < 64:
        raise ValueError("truncated ARM64 Image header")
    code0, code1 = struct.unpack_from("<II", image)
    text_offset, image_size, flags = struct.unpack_from("<QQQ", image, 8)
    magic, = struct.unpack_from("<I", image, 56)
    pe_offset, = struct.unpack_from("<I", image, 60)
    if magic != 0x644D5241:
        raise ValueError("not an ARM64 Linux Image")
    if flags & 1 or flags & ~0xF:
        raise ValueError("unsupported Image endianness or reserved flags")
    if (flags >> 1) & 3 != 1:
        raise ValueError("stage0 requires the configured 4 KiB Image page size")
    # Pinned upstream head.S emits an ordinary NOP then B primary_entry
    # when EFI is disabled. The branch PC is byte 4, not the load address.
    if code0 != 0xD503201F or code1 & 0xFC000000 != 0x14000000 or pe_offset:
        raise ValueError("Image does not have the pinned non-EFI NOP/branch header")
    if any(image[32:56]):
        raise ValueError("Image reserved header words must be zero")
    imm26 = code1 & 0x03FFFFFF
    if imm26 & 0x02000000:
        imm26 -= 0x04000000
    target = 4 + imm26 * 4
    if not 64 <= target < len(image):
        raise ValueError("Image entry branch is outside its payload")
    if text_offset != 0 or (PAYLOAD_ENTRY - text_offset) % (2 * 1024 * 1024):
        raise ValueError("Image text offset/alignment does not match the planned entry")
    if not image_size or image_size < len(image):
        raise ValueError("missing or undersized Image memory requirement")
    end = PAYLOAD_ENTRY + image_size
    if end > TIMA_START:
        raise ValueError("Image memory span overlaps the firmware TIMA log reservation")
    return {"file_bytes": len(image), "required_memory_bytes": image_size,
            "text_offset": text_offset, "flags": flags, "entry_branch_offset": target,
            "entry_instruction_offset": 4,
            "planned_load_address": PAYLOAD_ENTRY, "planned_memory_end": end,
            "hardware_boot_tested": False, "flashable_android_image": False,
            "second_header_word": code1}


def validate_init_elf(elf: bytes) -> dict:
    if len(elf) < 64 or elf[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("/init is not little-endian ELF64")
    elf_type, machine = struct.unpack_from("<HH", elf, 16)
    if elf_type != 2 or machine != 183:
        raise ValueError("/init must be a static AArch64 executable, not a host binary or PIE")
    phoff, = struct.unpack_from("<Q", elf, 32)
    entry, = struct.unpack_from("<Q", elf, 24)
    version, = struct.unpack_from("<I", elf, 20)
    ehsize, = struct.unpack_from("<H", elf, 52)
    if version != 1 or ehsize != 64:
        raise ValueError("invalid /init ELF header version or size")
    phentsize, phnum = struct.unpack_from("<HH", elf, 54)
    if phentsize != 56 or not phnum or phoff < 64 or phoff + phentsize * phnum > len(elf):
        raise ValueError("invalid /init program-header bounds")
    has_load = False
    has_entry = False
    for index in range(phnum):
        p_type, flags, offset, vaddr, _, filesz, memsz, align = struct.unpack_from(
            "<IIQQQQQQ", elf, phoff + index * phentsize)
        if p_type in (2, 3):
            raise ValueError("/init must not require dynamic loading or an ELF interpreter")
        if offset + filesz > len(elf):
            raise ValueError("/init segment exceeds file bounds")
        if p_type == 1:
            if (filesz > memsz or vaddr + memsz > 2**64 or flags & ~7
                    or align > 1 and (align & (align - 1) or offset % align != vaddr % align)):
                raise ValueError("invalid /init loadable segment")
            has_load = True
            has_entry |= bool(flags & 1 and vaddr <= entry < vaddr + filesz)
    if not has_load:
        raise ValueError("/init has no loadable segment")
    if not has_entry:
        raise ValueError("/init entry is not backed by an executable segment")
    return {"file_bytes": len(elf), "architecture": "aarch64", "static": True,
            "entry_address": entry}


def validate_initramfs(compressed: bytes) -> dict:
    if len(compressed) > MAX_INITRAMFS:
        raise ValueError("initramfs exceeds the planned low-memory arena")
    # Bound decompression as well, even though CI supplies our own archive.
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
        raw = archive.read(MAX_INITRAMFS + 1)
    if len(raw) > MAX_INITRAMFS:
        raise ValueError("uncompressed initramfs exceeds stage0 limits")
    if len(raw) % 512:
        raise ValueError("initramfs does not have the expected archive block padding")
    entries = parse_newc(raw)
    expected = {".", "dev", "proc", "sys", "tmp", "dev/console", "dev/kmsg", "init"}
    if set(entries) != expected:
        raise ValueError("unexpected initramfs contents")
    for name, entry in entries.items():
        if (entry["uid"] or entry["gid"] or entry["mtime"] or entry["devmajor"]
                or entry["devminor"] or entry["nlink"] != 1):
            raise ValueError("initramfs metadata is not root-owned/deterministic")
        if name in (".", "dev", "proc", "sys", "tmp"):
            permissions = 0o1777 if name == "tmp" else 0o755
            if entry["mode"] != stat.S_IFDIR | permissions or entry["data"]:
                raise ValueError("invalid initramfs mountpoint")
    for name, major, minor in (("dev/console", 5, 1), ("dev/kmsg", 1, 11)):
        entry = entries[name]
        if (entry["mode"] != stat.S_IFCHR | 0o600 or entry["rdevmajor"] != major
                or entry["rdevminor"] != minor or entry["data"]):
            raise ValueError(f"invalid {name} device node")
    init = entries["init"]
    if init["mode"] != stat.S_IFREG | 0o755 or init["rdevmajor"] or init["rdevminor"]:
        raise ValueError("/init is not an executable regular file")
    elf = validate_init_elf(init["data"])
    return {"compressed_bytes": len(compressed), "archive_bytes": len(raw),
            "entries": sorted(entries), "init": elf,
            "planned_load_address": RAMDISK_ENTRY,
            "planned_initrd_end": RAMDISK_ENTRY + len(compressed)}


def _fdt(path: Path, node: str, prop: str, kind: str = "x") -> list:
    result = subprocess.run(["fdtget", "-t", kind, str(path), node, prop],
                            check=True, capture_output=True, text=True)
    if kind == "x":
        return [int(cell, 16) for cell in result.stdout.split()]
    return result.stdout.strip().split()


def validate_dtb(path: Path) -> dict:
    checks = {
        ("/", "compatible", "s"): ["samsung,on5xelte", "samsung,exynos7570"],
        ("/", "#address-cells", "x"): [2],
        ("/", "#size-cells", "x"): [2],
        ("/memory@40000000", "reg", "x"): [0, 0x40000000, 0, 0x20000000],
        ("/clock-osc", "clock-frequency", "x"): [26000000],
        ("/psci", "compatible", "s"): ["arm,psci"],
        ("/psci", "method", "s"): ["smc"],
        ("/psci", "cpu_suspend", "x"): [0xC4000001],
        ("/psci", "cpu_off", "x"): [0x84000002],
        ("/psci", "cpu_on", "x"): [0xC4000003],
        ("/timer", "compatible", "s"): ["samsung,exynos7570-timer", "arm,armv8-timer"],
        ("/timer", "clock-frequency", "x"): [26000000],
        ("/soc/interrupt-controller@104e1000", "reg", "x"):
            [0, 0x104E1000, 0, 0x1000, 0, 0x104E2000, 0, 0x1000,
             0, 0x104E4000, 0, 0x2000, 0, 0x104E6000, 0, 0x2000],
        ("/soc/timer@101b0000", "compatible", "s"):
            ["samsung,exynos7570-mct", "samsung,exynos4210-mct"],
        ("/soc/timer@101b0000", "reg", "x"): [0, 0x101B0000, 0, 0x800],
        ("/soc/timer@101b0000", "interrupts", "x"):
            [cell for spi in range(102, 114) for cell in (0, spi, 4)],
        ("/reserved-memory/firmware-logs@46000000", "reg", "x"):
            [0, 0x46000000, 0, 0x01000000],
        ("/reserved-memory/loader@47000000", "reg", "x"):
            [0, 0x47000000, 0, 0x02000000],
        ("/reserved-memory/tima-log@52800000", "reg", "x"):
            [0, TIMA_START, 0, 0x00200000],
        ("/chosen/framebuffer@67000000", "status", "s"): ["disabled"],
        ("/chosen/framebuffer@67000000", "width", "x"): [720],
        ("/chosen/framebuffer@67000000", "height", "x"): [1280],
        ("/chosen/framebuffer@67000000", "stride", "x"): [2880],
        ("/aliases", "display0", "s"): ["/chosen/framebuffer@67000000"],
    }
    for (node, prop, kind), expected in checks.items():
        if _fdt(path, node, prop, kind) != expected:
            raise ValueError(f"DTB mismatch: {node}:{prop}")
    for index in range(4):
        if _fdt(path, f"/cpus/cpu@{index}", "reg") != [0, index]:
            raise ValueError("DTB CPU affinity does not match Exynos7570")
    bootargs = " ".join(_fdt(path, "/chosen", "bootargs", "s"))
    for required in ("maxcpus=1", "console=tty0", "rdinit=/init", "consoleblank=0"):
        if required not in bootargs.split():
            raise ValueError(f"DTB missing stage0 argument {required}")
    for forbidden in ("androidboot", "earlycon", "ess_setup", "tima_log"):
        if forbidden in bootargs:
            raise ValueError("DTB contains downstream or unvalidated early-console arguments")
    return {"property_checks": len(checks), "cpu_checks": 4,
            "framebuffer_enabled": False, "usable_dram_bytes": 512 * 1024 * 1024,
            "hardware_boot_tested": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("image", "elf", "initramfs", "config", "dtb"))
    parser.add_argument("path", type=Path)
    parser.add_argument("fragment", type=Path, nargs="?")
    args = parser.parse_args()
    try:
        if args.kind == "config":
            if args.fragment is None:
                parser.error("config validation requires the requested fragment")
            report = validate_config(args.path.read_text(), args.fragment.read_text())
        elif args.kind == "dtb":
            report = validate_dtb(args.path)
        else:
            validator = {"image": validate_image, "elf": validate_init_elf,
                         "initramfs": validate_initramfs}[args.kind]
            report = validator(args.path.read_bytes())
    except (ValueError, OSError, EOFError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Validation failed: {error}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
