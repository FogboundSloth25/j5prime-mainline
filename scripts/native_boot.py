#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate/inspect a BOOT-only diagnostic wrapper; never use device templates."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
from scripts.make_initramfs import _record, compress_newc, parse_newc
from scripts.validate import (NATIVE_BOOTARGS, NATIVE_ENTRY, RAMDISK_ENTRY,
                              validate_dtb, validate_image)

PAGE = 2048
WRITE_BLOCK = 4096
PARTITION_BYTES = 32 * 1024 * 1024
FDT_REQUEST = 0x47F00000
MAX_DTB = 2 * 1024 * 1024
BOARD = b"on5xelte-stage0"
FOOTER = b"SEANDROIDENFORCE" + b"\xff" * 4
# Sanitized non-unique Exynos7570/on5xe SWA identity, not a copied firmware DTB.
DTBH_IDENTITY = (7570, 20646, 561349850, 4, 4)
BOOTARGS = NATIVE_BOOTARGS


def align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def empty_newc() -> bytes:
    raw = _record("TRAILER!!!", b"", 0, 1)
    return raw + b"\0" * (-len(raw) % 512)


def compatibility_ramdisk() -> bytes:
    # Nonzero Android legacy ramdisk field, but no Android files or second /init.
    return compress_newc(empty_newc())


def validate_compatibility_ramdisk(data: bytes) -> dict:
    if not data or len(data) > PAGE:
        raise ValueError("invalid compatibility ramdisk size")
    if data != compatibility_ramdisk():
        raise ValueError("compatibility ramdisk must be the exact deterministic trailer-only gzip")
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as archive:
        raw = archive.read(513)
    if raw != empty_newc() or parse_newc(raw):
        raise ValueError("external compatibility ramdisk must contain only a trailer")
    if data[:10] != b"\x1f\x8b\x08\x00\0\0\0\0\x02\xff":
        raise ValueError("compatibility ramdisk has non-deterministic gzip metadata")
    return {"compressed_bytes": len(data), "archive_bytes": len(raw), "entries": [],
            "userspace_delivery": "built-in initramfs, not this empty compatibility ramdisk"}


def validate_fdt_header(dtb: bytes) -> dict:
    # Format bounds AND token grammar only. CLI/build also run strict board,
    # topology and schema checks. This is never board authentication.
    if len(dtb) < 40 or len(dtb) > MAX_DTB:
        raise ValueError("invalid FDT size")
    magic, total, structure, strings, reserve, version, last, _, strings_size, structure_size = \
        struct.unpack_from(">10I", dtb)
    if magic != 0xD00DFEED or total != len(dtb) or version != 17 or last != 16:
        raise ValueError("unsupported FDT header")
    if (structure < 40 or structure % 4 or structure_size < 16 or
            structure + structure_size > total or strings < 40 or
            not strings_size or strings + strings_size > total or reserve < 40 or reserve % 8):
        raise ValueError("FDT block bounds/alignment")
    end = reserve
    reservations = 0
    while True:
        if end + 16 > total:
            raise ValueError("unterminated FDT reservation table")
        address, size = struct.unpack_from(">QQ", dtb, end)
        end += 16
        if not address and not size:
            break
        if not size or address + size >= 2**64 or reservations >= 64:
            raise ValueError("invalid FDT reservation span")
        reservations += 1
    regions = [(structure, structure + structure_size), (reserve, end)]
    if strings_size:
        regions.append((strings, strings + strings_size))
    for index, (start, finish) in enumerate(regions):
        for other_start, other_end in regions[index + 1:]:
            if start < other_end and other_start < finish:
                raise ValueError("overlapping FDT blocks")
    position = structure
    structure_end = structure + structure_size
    children = []
    root_closed = False
    tokens = 0
    while position < structure_end:
        token_start = position
        if position + 4 > structure_end:
            raise ValueError("truncated FDT structure token")
        tag, = struct.unpack_from(">I", dtb, position)
        position += 4
        tokens += 1
        if tag == 1:  # FDT_BEGIN_NODE
            name_end = dtb.find(b"\0", position, structure_end)
            if name_end < 0:
                raise ValueError("unterminated FDT node name")
            name = dtb[position:name_end]
            if (root_closed or len(children) >= 32 or len(name) > 255 or b"/" in name or
                    (not children and (token_start != structure or name)) or
                    (children and not name)):
                raise ValueError("invalid FDT root/node/depth")
            position = align(name_end + 1, 4)
            if position > structure_end or any(dtb[name_end + 1:position]):
                raise ValueError("FDT node padding/bounds")
            if children:
                children[-1] = True
            children.append(False)
        elif tag == 3:  # FDT_PROP
            if not children or children[-1] or position + 8 > structure_end:
                raise ValueError("invalid FDT property position/header")
            size, name_offset = struct.unpack_from(">II", dtb, position)
            position += 8
            if name_offset >= strings_size:
                raise ValueError("FDT property name exceeds strings block")
            name_start = strings + name_offset
            name_end = dtb.find(b"\0", name_start, strings + strings_size)
            if name_end <= name_start or name_end - name_start > 255:
                raise ValueError("invalid FDT property name")
            data_end = position + size
            position = align(data_end, 4)
            if position > structure_end or any(dtb[data_end:position]):
                raise ValueError("FDT property data/padding bounds")
        elif tag == 2:  # FDT_END_NODE
            if not children:
                raise ValueError("unbalanced FDT node close")
            children.pop()
            root_closed |= not children
        elif tag == 4:  # FDT_NOP
            if token_start == structure:
                raise ValueError("FDT root must start at structure offset zero")
        elif tag == 9:  # FDT_END
            if children or not root_closed or position != structure_end:
                raise ValueError("premature or trailing FDT structure end")
            break
        else:
            raise ValueError("unsupported FDT structure token")
    else:
        raise ValueError("FDT structure has no final end token")
    return {"fdt_bytes": total, "reservation_entries": reservations, "structure_tokens_checked": tokens,
            "board_topology_validated_by_this_header_check": False}


def pack_dtbh(dtb: bytes) -> bytes:
    validate_fdt_header(dtb)
    size = align(len(dtb), PAGE)
    table = bytearray(PAGE)
    struct.pack_into("<4sII", table, 0, b"DTBH", 2, 1)
    struct.pack_into("<8I", table, 12, *DTBH_IDENTITY, PAGE, size, 32)
    table.extend(dtb)
    table.extend(b"\0" * (size - len(dtb)))
    validate_dtbh(table)
    return bytes(table)


def validate_dtbh(table: bytes) -> dict:
    if len(table) < 2 * PAGE or table[:4] != b"DTBH":
        raise ValueError("not a complete generated DTBH table")
    version, count = struct.unpack_from("<II", table, 4)
    fields = struct.unpack_from("<8I", table, 12)
    if version != 2 or count != 1 or fields[:5] != DTBH_IDENTITY:
        raise ValueError("DTBH must contain exactly the diagnostic SWA rev04 identity")
    offset, size, entry_bytes = fields[5:]
    if offset != PAGE or size % PAGE or size < 40 or entry_bytes != 32 or len(table) != offset + size:
        raise ValueError("DTBH entry bounds/alignment/entry-size")
    if any(table[44:PAGE]):
        raise ValueError("nonzero DTBH header padding")
    total, = struct.unpack_from(">I", table, offset + 4)
    if not 40 <= total <= size or size != align(total, PAGE):
        raise ValueError("DTBH padded FDT bounds")
    dtb = bytes(table[offset:offset + total])
    report = validate_fdt_header(dtb)
    if any(table[offset + total:]):
        raise ValueError("nonzero DTBH component padding")
    return {"version": version, "count": count, "chip": fields[0],
            "platform": fields[1], "subtype": fields[2], "hw_rev_start": 4,
            "hw_rev_end": 4, "offset": offset, "padded_bytes": size,
            "entry_bytes": entry_bytes, "fdt_sha256": hashlib.sha256(dtb).hexdigest(),
            **report}


def _header(image: bytes, ramdisk: bytes, dtbh: bytes) -> bytes:
    header = bytearray(PAGE)
    header[:8] = b"ANDROID!"
    struct.pack_into("<10I", header, 8, len(image), NATIVE_ENTRY,
                     len(ramdisk), RAMDISK_ENTRY, 0, 0, FDT_REQUEST, PAGE, len(dtbh), 0)
    header[48:48 + len(BOARD)] = BOARD
    encoded = BOOTARGS.encode("ascii")
    if len(encoded) >= 512:
        raise ValueError("native diagnostic bootargs are too long")
    header[64:64 + len(encoded)] = encoded
    digest = hashlib.sha1()
    for component in (image, ramdisk, b"", dtbh):
        digest.update(component)
        digest.update(struct.pack("<I", len(component)))
    header[576:596] = digest.digest()
    return bytes(header)


def pack_boot(image: bytes, dtbh: bytes, ramdisk: bytes) -> bytes:
    validate_image(image, load_address=NATIVE_ENTRY)
    validate_dtbh(dtbh)
    validate_compatibility_ramdisk(ramdisk)
    result = bytearray(_header(image, ramdisk, dtbh))
    for component in (image, ramdisk, b"", dtbh):
        result.extend(component)
        result.extend(b"\0" * (-len(component) % PAGE))
    result.extend(FOOTER)
    result.extend(b"\0" * (-len(result) % WRITE_BLOCK))
    validate_boot(result)
    return bytes(result)


def validate_boot(data: bytes) -> dict:
    if (len(data) < 3 * PAGE or len(data) > PARTITION_BYTES or
            len(data) % WRITE_BLOCK or data[:8] != b"ANDROID!"):
        raise ValueError("BOOT size/magic/write alignment")
    fields = struct.unpack_from("<10I", data, 8)
    if (fields[1], fields[3], fields[4], fields[5], fields[6], fields[7], fields[9]) != \
            (NATIVE_ENTRY, RAMDISK_ENTRY, 0, 0, FDT_REQUEST, PAGE, 0):
        raise ValueError("BOOT header does not match the native requested layout")
    offset = PAGE
    components = []
    offsets = []
    for size in (fields[0], fields[2], fields[4], fields[8]):
        finish = offset + size
        padded_end = offset + align(size, PAGE)
        if padded_end > len(data):
            raise ValueError("truncated BOOT component")
        if any(data[finish:padded_end]):
            raise ValueError("nonzero BOOT component padding")
        components.append(bytes(data[offset:finish]))
        offsets.append(offset)
        offset = padded_end
    image, ramdisk, _, dtbh = components
    if data[:PAGE] != _header(image, ramdisk, dtbh):
        raise ValueError("BOOT metadata/legacy component ID/header padding mismatch")
    footer_end = offset + len(FOOTER)
    if (data[offset:footer_end] != FOOTER or len(data) != align(footer_end, WRITE_BLOCK) or
            any(data[footer_end:])):
        raise ValueError("BOOT footer/trailer/padding mismatch")
    image_report = validate_image(image, load_address=NATIVE_ENTRY)
    dt_report = validate_dtbh(dtbh)
    ramdisk_report = validate_compatibility_ramdisk(ramdisk)
    return {"partition": "BOOT", "partition_bytes": PARTITION_BYTES, "image_bytes": len(data),
            "image_sha256": hashlib.sha256(data).hexdigest(), "write_block_bytes": WRITE_BLOCK,
            "page_bytes": PAGE, "board": BOARD.decode(), "hardware_revision": 4,
            "component_offsets": dict(zip(("kernel", "ramdisk", "second", "dtbh"), offsets)),
            "component_sha256": dict(zip(("kernel", "ramdisk", "second", "dtbh"),
                                        (hashlib.sha256(c).hexdigest() for c in components))),
            "legacy_sha1_id": data[576:596].hex(), "native_image": image_report,
            "dtbh": dt_report, "compatibility_ramdisk": ramdisk_report,
            "footer_hex": FOOTER.hex(), "private_firmware_template_used": False,
            "firmware_acceptance_and_actual_placement_verified": False,
            "hardware_boot_tested": False, "phone_write_authorized": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("pack", "verify"))
    parser.add_argument("--image", type=Path)
    parser.add_argument("--dtb", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.kind == "pack":
        if args.image is None or args.dtb is None:
            parser.error("pack requires --image and --dtb")
        board_report = validate_dtb(args.dtb)
        dtbh = pack_dtbh(args.dtb.read_bytes())
        ramdisk = compatibility_ramdisk()
        data = pack_boot(args.image.read_bytes(), dtbh, ramdisk)
        with args.output.open("xb") as file:
            file.write(data)
        with args.output.with_suffix(".dtbh").open("xb") as file:
            file.write(dtbh)
        with args.output.with_suffix(".empty-ramdisk.cpio.gz").open("xb") as file:
            file.write(ramdisk)
        report = validate_boot(data)
        report["board_dtb_validation"] = board_report
    else:
        report = validate_boot(args.output.read_bytes())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
