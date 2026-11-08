#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Create a deterministic newc initramfs without root, mknod or host mounts."""

import argparse
import gzip
import io
from pathlib import Path, PurePosixPath
import re
import stat


def _pad4(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 4)


def _record(name: str, data: bytes, mode: int, ino: int,
            rdevmajor: int = 0, rdevminor: int = 0) -> bytes:
    path = PurePosixPath(name)
    if (not name or "\0" in name or path.is_absolute()
            or ".." in path.parts or str(path) != name
            or not re.fullmatch(r"[A-Za-z0-9._!/-]+", name)):
        raise ValueError(f"unsafe archive path: {name!r}")
    encoded_name = name.encode("ascii") + b"\0"
    fields = (ino, mode, 0, 0, 1, 0, len(data), 0, 0,
              rdevmajor, rdevminor, len(encoded_name), 0)
    if any(not 0 <= field <= 0xFFFFFFFF for field in fields):
        raise ValueError("newc field overflow")
    header = b"070701" + "".join(f"{field:08x}" for field in fields).encode("ascii")
    return _pad4(header + encoded_name) + _pad4(data)


def build_newc(init: bytes) -> bytes:
    if not init:
        raise ValueError("empty /init")
    entries = [
        (".", b"", stat.S_IFDIR | 0o755, 0, 0),
        ("dev", b"", stat.S_IFDIR | 0o755, 0, 0),
        ("proc", b"", stat.S_IFDIR | 0o755, 0, 0),
        ("sys", b"", stat.S_IFDIR | 0o755, 0, 0),
        ("tmp", b"", stat.S_IFDIR | 0o1777, 0, 0),
        ("dev/console", b"", stat.S_IFCHR | 0o600, 5, 1),
        ("dev/kmsg", b"", stat.S_IFCHR | 0o600, 1, 11),
        ("init", init, stat.S_IFREG | 0o755, 0, 0),
        ("TRAILER!!!", b"", 0, 0, 0),
    ]
    archive = b"".join(_record(name, data, mode, ino, major, minor)
                       for ino, (name, data, mode, major, minor)
                       in enumerate(entries, 1))
    return archive + b"\0" * (-len(archive) % 512)


def compress_newc(archive: bytes) -> bytes:
    # GzipFile fixes OS=255 on all supported Python versions and does not
    # embed a host filename. gzip.compress(mtime=0) differs on Python 3.12.
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output,
                       compresslevel=9, mtime=0) as compressed:
        compressed.write(archive)
    return output.getvalue()


def parse_newc(archive: bytes) -> dict:
    """Strictly inspect this stage's archive; never extract to the host."""
    offset = 0
    result = {}
    while True:
        if offset + 110 > len(archive) or archive[offset:offset + 6] != b"070701":
            raise ValueError("truncated or invalid newc header")
        encoded_fields = archive[offset + 6:offset + 110]
        if not re.fullmatch(rb"[0-9a-fA-F]{104}", encoded_fields):
            raise ValueError("invalid newc field")
        fields = [int(encoded_fields[i * 8:(i + 1) * 8], 16) for i in range(13)]
        if fields[12]:
            raise ValueError("newc checksum field must be zero")
        size, namesize = fields[6], fields[11]
        name_start = offset + 110
        name_end = name_start + namesize
        if namesize < 2 or name_end > len(archive):
            raise ValueError("invalid newc name bounds")
        raw_name = archive[name_start:name_end]
        if raw_name[-1] != 0 or b"\0" in raw_name[:-1]:
            raise ValueError("invalid newc name terminator")
        try:
            name = raw_name[:-1].decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("non-ASCII newc name") from error
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or str(path) != name
                or not re.fullmatch(r"[A-Za-z0-9._!/-]+", name)):
            raise ValueError("unsafe newc name")
        data_start = (name_end + 3) & ~3
        data_end = data_start + size
        next_offset = (data_end + 3) & ~3
        if next_offset > len(archive):
            raise ValueError("invalid newc file bounds")
        if any(archive[name_end:data_start]) or any(archive[data_end:next_offset]):
            raise ValueError("non-zero newc alignment padding")
        offset = next_offset
        if name == "TRAILER!!!":
            if fields[1] or size or any(archive[offset:]):
                raise ValueError("invalid trailer or non-zero trailing data")
            return result
        if name in result:
            raise ValueError("duplicate newc path")
        result[name] = {
            "ino": fields[0], "nlink": fields[4],
            "mode": fields[1], "uid": fields[2], "gid": fields[3],
            "devmajor": fields[7], "devminor": fields[8],
            "mtime": fields[5], "rdevmajor": fields[9], "rdevminor": fields[10],
            "data": archive[data_start:data_end],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archive = build_newc(args.init.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(compress_newc(archive))


if __name__ == "__main__":
    main()
