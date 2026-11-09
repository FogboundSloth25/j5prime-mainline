# SPDX-License-Identifier: GPL-2.0-only
"""BOOT/DTBH mutations and an independent legacy-ID reader, CI only."""
import gzip
import hashlib
import struct
import subprocess
import sys
import unittest
from scripts.make_initramfs import build_newc, compress_newc, parse_newc
from scripts.native_boot import (BOARD, BOOTARGS, DTBH_IDENTITY, FDT_REQUEST, FOOTER,
                                 NATIVE_ENTRY, PAGE, PARTITION_BYTES, RAMDISK_ENTRY,
                                 compatibility_ramdisk, empty_newc, pack_boot, pack_dtbh,
                                 validate_boot, validate_compatibility_ramdisk,
                                 validate_dtbh, validate_fdt_header)
from tests.native_support import synthetic_fdt
from tests.support import ROOT, synthetic_elf, synthetic_image, temporary_directory
from pathlib import Path


class NativeBootTests(unittest.TestCase):
    def setUp(self):
        self.dtb = synthetic_fdt()
        self.image = bytes(synthetic_image())
        self.table = pack_dtbh(self.dtb)
        self.ramdisk = compatibility_ramdisk()
        self.boot = pack_boot(self.image, self.table, self.ramdisk)

    def test_generated_dtbh_has_one_exact_rev04_entry_and_size_field(self):
        self.assertEqual(struct.unpack_from("<4sII", self.table), (b"DTBH", 2, 1))
        self.assertEqual(struct.unpack_from("<8I", self.table, 12),
                         (*DTBH_IDENTITY, PAGE, PAGE, 32))
        self.assertEqual(self.table[PAGE:PAGE + len(self.dtb)], self.dtb)
        self.assertFalse(any(self.table[44:PAGE]))
        self.assertFalse(any(self.table[PAGE + len(self.dtb):]))
        report = validate_dtbh(self.table)
        self.assertEqual(report["entry_bytes"], 32)
        self.assertEqual(report["fdt_sha256"], hashlib.sha256(self.dtb).hexdigest())
        self.assertFalse(report["board_topology_validated_by_this_header_check"])

    def test_independent_header_component_id_and_footer_reader(self):
        fields = struct.unpack_from("<10I", self.boot, 8)
        self.assertEqual(self.boot[:8], b"ANDROID!")
        self.assertEqual((fields[1], fields[3], fields[4], fields[5], fields[6], fields[7], fields[9]),
                         (NATIVE_ENTRY, RAMDISK_ENTRY, 0, 0, FDT_REQUEST, PAGE, 0))
        self.assertEqual(self.boot[48:64].rstrip(b"\0"), BOARD)
        self.assertEqual(self.boot[64:576].rstrip(b"\0").decode(), BOOTARGS)
        offset, digest, components = PAGE, hashlib.sha1(), []
        for size in (fields[0], fields[2], fields[4], fields[8]):
            data = self.boot[offset:offset + size]
            components.append(data)
            digest.update(data); digest.update(struct.pack("<I", size))
            offset += (size + PAGE - 1) // PAGE * PAGE
        self.assertEqual(components, [self.image, self.ramdisk, b"", self.table])
        self.assertEqual(self.boot[576:596], digest.digest())
        self.assertEqual(self.boot[offset:offset + len(FOOTER)], FOOTER)
        self.assertFalse(any(self.boot[offset + len(FOOTER):]))
        self.assertEqual(len(self.boot) % 4096, 0)
        self.assertLessEqual(len(self.boot), PARTITION_BYTES)
        report = validate_boot(self.boot)
        self.assertEqual(report["partition"], "BOOT")
        self.assertFalse(report["private_firmware_template_used"])
        self.assertFalse(report["phone_write_authorized"])
        self.assertFalse(report["firmware_acceptance_and_actual_placement_verified"])
        self.assertFalse(report["hardware_boot_tested"])

    def test_empty_external_archive_cannot_replace_built_in_init(self):
        self.assertEqual(gzip.decompress(self.ramdisk), empty_newc())
        self.assertEqual(parse_newc(empty_newc()), {})
        for data in (b"", self.ramdisk[:-1], self.ramdisk + gzip.compress(b""),
                     compress_newc(build_newc(bytes(synthetic_elf())))):
            with self.subTest(bytes=len(data)), self.assertRaises((ValueError, OSError, EOFError)):
                validate_compatibility_ramdisk(data)
        self.assertEqual(validate_compatibility_ramdisk(self.ramdisk)["entries"], [])

    def test_dtbh_identity_bounds_count_and_padding_mutations(self):
        for offset, value in ((4,1),(8,2),(12,0),(16,0),(20,0),(24,3),(28,5),
                              (32,0),(36,PAGE-1),(40,0)):
            table = bytearray(self.table)
            struct.pack_into("<I", table, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_dtbh(table)
        for offset in (0, 44, PAGE + len(self.dtb), len(self.table)-1):
            table = bytearray(self.table); table[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_dtbh(table)
        for table in (self.table[:-1], self.table + b"\0" * PAGE):
            with self.assertRaises(ValueError):
                validate_dtbh(table)

    def test_fdt_header_blocks_and_structure_are_not_trusted(self):
        for offset, value in ((0,0),(4,len(self.dtb)+1),(8,57),(12,56),(16,57),
                              (20,16),(24,17),(32,2**31),(36,4),(56,2)):
            dtb = bytearray(self.dtb); struct.pack_into(">I", dtb, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_fdt_header(dtb)
        dtb = bytearray(self.dtb)
        structure_size, = struct.unpack_from(">I", dtb, 36)
        struct.pack_into(">I", dtb, 56 + structure_size - 4, 0)
        with self.assertRaisesRegex(ValueError, "structure token"):
            validate_fdt_header(dtb)
        dtb = bytearray(self.dtb); dtb[40:56] = b"\xff" * 16
        with self.assertRaises(ValueError):
            validate_fdt_header(dtb)

    def test_native_image_and_requested_layout_mutations(self):
        for offset, value in ((12,0x40080000),(20,0x43000000),(24,1),(28,1),
                              (32,0x48000000),(36,4096),(44,1)):
            boot = bytearray(self.boot); struct.pack_into("<I", boot, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_boot(boot)
        for offset in (48, 64, 576, 600, PAGE+64, PAGE+len(self.image)):
            boot = bytearray(self.boot); boot[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_boot(boot)
        for boot in (self.boot[:-1], self.boot + b"\0" * 4096, b"x" * (PARTITION_BYTES+1)):
            with self.assertRaises(ValueError):
                validate_boot(boot)
        boot = bytearray(self.boot); boot[-1] = 1
        with self.assertRaises(ValueError):
            validate_boot(boot)
        image = synthetic_image(); struct.pack_into("<Q", image, 8, 0x80000)
        with self.assertRaises(ValueError):
            pack_boot(image, self.table, self.ramdisk)
        image = synthetic_image(); struct.pack_into("<Q", image, 16, 0x2000001)
        with self.assertRaisesRegex(ValueError, "native"):
            pack_boot(image, self.table, self.ramdisk)

    def test_cli_does_not_publish_an_unvalidated_board_fixture(self):
        with temporary_directory() as directory:
            root = Path(directory)
            (root/"Image").write_bytes(self.image); (root/"fixture.dtb").write_bytes(self.dtb)
            output = root/"candidate.img"
            result = subprocess.run([sys.executable, "-m", "scripts.native_boot", "pack",
                                     "--image", str(root/"Image"), "--dtb", str(root/"fixture.dtb"),
                                     "--output", str(output)], cwd=ROOT, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
