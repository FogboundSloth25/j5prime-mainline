# SPDX-License-Identifier: GPL-2.0-only

import gzip
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.make_initramfs import _record, build_newc, compress_newc, parse_newc
from scripts.validate import validate_initramfs
from tests.support import ROOT, synthetic_elf, temporary_directory


def change_field(archive, name, field, value):
    """Mutate a known synthetic record, retaining the other bytes."""
    result = bytearray(archive)
    offset = 0
    while offset < len(result):
        size = int(result[offset + 54:offset + 62], 16)
        namesize = int(result[offset + 94:offset + 102], 16)
        found = result[offset + 110:offset + 110 + namesize - 1].decode("ascii")
        if found == name:
            result[offset + 6 + field * 8:offset + 14 + field * 8] = f"{value:08x}".encode()
            return bytes(result)
        offset = (((offset + 110 + namesize + 3) & ~3) + size + 3) & ~3
    raise AssertionError("fixture record not found")


class NewcTests(unittest.TestCase):
    def setUp(self):
        self.init = bytes(synthetic_elf())
        self.archive = build_newc(self.init)

    def test_roundtrip_and_expected_contents(self):
        entries = parse_newc(self.archive)
        self.assertEqual(set(entries), {".", "dev", "proc", "sys", "tmp", "dev/console", "dev/kmsg", "init"})
        self.assertEqual(entries["init"]["data"], self.init)
        self.assertEqual(entries["init"]["mode"], stat.S_IFREG | 0o755)
        self.assertEqual(len(self.archive) % 512, 0)

    def test_metadata_does_not_depend_on_host_uid_or_time(self):
        for entry in parse_newc(self.archive).values():
            self.assertEqual((entry["uid"], entry["gid"], entry["mtime"]), (0, 0, 0))

    def test_archive_and_gzip_are_deterministic(self):
        self.assertEqual(self.archive, build_newc(self.init))
        first = compress_newc(self.archive)
        self.assertEqual(first, compress_newc(self.archive))
        self.assertEqual(first[:10], b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff")
        self.assertEqual(gzip.decompress(first), self.archive)

    def test_character_devices(self):
        entries = parse_newc(self.archive)
        for name, major, minor in (("dev/console", 5, 1), ("dev/kmsg", 1, 11)):
            entry = entries[name]
            self.assertEqual((entry["rdevmajor"], entry["rdevminor"]), (major, minor))
            self.assertEqual(entry["mode"], stat.S_IFCHR | 0o600)

    def test_empty_init_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            build_newc(b"")

    def test_paths_cannot_escape_or_hide_control_bytes(self):
        for name in ("", "/init", "../init", "dev/../init", "./init", "a//b",
                     "init/", "a\0b", "a\nb", "a\\b", "täst"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _record(name, b"", stat.S_IFREG | 0o755, 1)

    def test_field_overflow(self):
        for ino in (-1, 2**32):
            with self.subTest(ino=ino), self.assertRaises(ValueError):
                _record("init", b"x", stat.S_IFREG | 0o755, ino)

    def test_truncated_archives(self):
        for length in (0, 6, 109, 111, 113, 500):
            with self.subTest(length=length), self.assertRaises(ValueError):
                parse_newc(self.archive[:length])

    def test_invalid_magic_and_hex_field(self):
        for offset, content in ((0, b"070702"), (6, b"-0000001"), (6, b" 0000001"),
                                (6, b"0000000g")):
            archive = bytearray(self.archive)
            archive[offset:offset + len(content)] = content
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_newc(archive)

    def test_name_bounds_and_terminator(self):
        for size in (0, 1, 0xFFFFFFFF):
            with self.subTest(size=size), self.assertRaises(ValueError):
                parse_newc(change_field(self.archive, ".", 11, size))
        archive = bytearray(self.archive)
        archive[111] = ord("x")
        with self.assertRaisesRegex(ValueError, "terminator"):
            parse_newc(archive)

    def test_parser_rejects_unsafe_and_non_ascii_names(self):
        for value in (ord("/"), 0xFF):
            archive = bytearray(self.archive)
            archive[110] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_newc(archive)

    def test_newc_checksum_is_zero(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            parse_newc(change_field(self.archive, ".", 12, 1))

    def test_nonzero_alignment_padding(self):
        archive = bytearray(self.archive)
        # Second record 'dev' ends its name at byte 226; bytes 226–227 pad it.
        archive[226] = 1
        with self.assertRaisesRegex(ValueError, "padding"):
            parse_newc(archive)

    def test_duplicate_name(self):
        duplicate = (_record(".", b"", stat.S_IFDIR | 0o755, 1)
                     + _record(".", b"", stat.S_IFDIR | 0o755, 2)
                     + _record("TRAILER!!!", b"", 0, 3))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_newc(duplicate)

    def test_no_trailer(self):
        with self.assertRaisesRegex(ValueError, "header"):
            parse_newc(_record("init", self.init, stat.S_IFREG | 0o755, 1))

    def test_trailer_and_trailing_data(self):
        for archive in (self.archive + b"x", _record("TRAILER!!!", b"x", 0, 1)):
            with self.subTest(length=len(archive)), self.assertRaises(ValueError):
                parse_newc(archive)


class InitramfsValidationTests(unittest.TestCase):
    def setUp(self):
        self.archive = build_newc(bytes(synthetic_elf()))

    def test_full_stage0_archive(self):
        report = validate_initramfs(compress_newc(self.archive))
        self.assertEqual(report["planned_load_address"], 0x42000000)
        self.assertTrue(report["init"]["static"])

    def test_modes_device_numbers_and_metadata(self):
        mutations = (("init", 1, stat.S_IFREG | 0o4755),
                     ("init", 2, 1000), ("init", 3, 1000), ("init", 5, 1),
                     ("init", 4, 2), ("init", 7, 1), ("init", 9, 1),
                     ("proc", 1, stat.S_IFLNK | 0o755), ("tmp", 1, stat.S_IFDIR | 0o777),
                     ("dev/console", 9, 4), ("dev/kmsg", 10, 10),
                     ("dev/kmsg", 1, stat.S_IFCHR | 0o666))
        for name, field, value in mutations:
            raw = change_field(self.archive, name, field, value)
            with self.subTest(name=name, field=field), self.assertRaises(ValueError):
                validate_initramfs(compress_newc(raw))

    def test_init_must_be_static_aarch64(self):
        with self.assertRaisesRegex(ValueError, "ELF64"):
            validate_initramfs(compress_newc(build_newc(b"not a real executable")))

    def test_unexpected_or_missing_members(self):
        for raw in (build_newc(b"x").replace(b"init\0", b"oops\0"),
                    _record("TRAILER!!!", b"", 0, 1) + b"\0" * 388):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                validate_initramfs(compress_newc(raw))

    def test_compressed_and_uncompressed_limits(self):
        compressed = compress_newc(self.archive)
        with patch("scripts.validate.MAX_INITRAMFS", len(compressed) - 1):
            with self.assertRaisesRegex(ValueError, "initramfs exceeds"):
                validate_initramfs(compressed)
        with patch("scripts.validate.MAX_INITRAMFS", len(compressed) + 1):
            with self.assertRaisesRegex(ValueError, "uncompressed"):
                validate_initramfs(compressed)

    def test_corrupt_or_truncated_gzip(self):
        for data in (b"not gzip", compress_newc(self.archive)[:-4]):
            with self.subTest(length=len(data)), self.assertRaises((OSError, EOFError)):
                validate_initramfs(data)

    def test_cli_does_not_embed_host_path(self):
        from scripts.make_initramfs import main
        with temporary_directory() as directory:
            init = Path(directory) / "host-private-filename"
            output = Path(directory) / "initramfs.cpio.gz"
            init.write_bytes(synthetic_elf())
            with patch.object(sys, "argv", ["make_initramfs", "--init", str(init), "--output", str(output)]):
                main()
            self.assertNotIn(str(init).encode(), output.read_bytes())
            validate_initramfs(output.read_bytes())

    @unittest.skipUnless(shutil.which("cpio"), "optional independent cpio reader not installed")
    def test_independent_cpio_reader(self):
        result = subprocess.run(["cpio", "-it", "--quiet"], input=self.archive,
                                capture_output=True, cwd=ROOT, check=True)
        self.assertEqual(set(result.stdout.decode().splitlines()),
                         {".", "dev", "proc", "sys", "tmp", "dev/console", "dev/kmsg", "init"})


if __name__ == "__main__":
    unittest.main()
