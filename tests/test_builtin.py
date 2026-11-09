# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic linked Image/ELF mutations; the real linked archive is checked in CI."""
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
from scripts.validate_builtin import (REQUIRED_SYMBOLS, elf_bytes_at,
                                      kernel_symbols, validate_builtin)
from tests.native_support import linked_fixture
from tests.support import temporary_directory


class BuiltinTests(unittest.TestCase):
    def setUp(self):
        self.image, self.elf, self.archive, self.init, self.symbols = linked_fixture()
        directory = temporary_directory(); self.addCleanup(directory.cleanup)
        self.vmlinux = Path(directory.name)/"vmlinux"
        self.vmlinux.write_bytes(self.elf)

    def check(self, image=None, archive=None, init=None, symbols=None):
        with patch("scripts.validate_builtin.kernel_symbols", return_value=symbols or self.symbols):
            return validate_builtin(self.image if image is None else image, self.vmlinux,
                                    self.archive if archive is None else archive,
                                    self.init if init is None else init)

    def test_exact_linked_archive_and_diagnostic_init(self):
        report = self.check()
        self.assertTrue(report["built_in_initramfs_verified"])
        self.assertTrue(report["native_handoff_symbols_present"])
        self.assertEqual(report["linked_archive_bytes"], len(self.archive))
        self.assertEqual(report["image_archive_offset"], 512)
        self.assertEqual(report["image_size_symbol_offset"], 3840)
        self.assertEqual(report["initramfs"]["userspace_delivery"], "built-in Image")
        self.assertNotIn("planned_load_address", report["initramfs"])
        self.assertFalse(report["hardware_boot_tested"])

    def test_external_archive_bytes_are_not_proof_of_linkage(self):
        image = bytearray(self.image); image[512] ^= 1
        with self.assertRaisesRegex(ValueError, "not linked"):
            self.check(image=image)
        image = bytearray(self.image); struct.pack_into("<Q", image, 3840, len(self.archive)-1)
        with self.assertRaisesRegex(ValueError, "not linked"):
            self.check(image=image)
        elf = bytearray(self.elf); elf[256+512] ^= 1; self.vmlinux.write_bytes(elf)
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.check()

    def test_kernel_symbol_bounds_and_memory_size(self):
        for name, value in (("_end",self.symbols["_end"]+1),
                            ("__initramfs_start",self.symbols["_text"]),
                            ("__initramfs_size",self.symbols["_text"]+len(self.image)),
                            ("on5xelte_stage0_fb_prepare",self.symbols["_text"]),
                            ("on5xelte_stage0_fb_register",self.symbols["_end"]+1)):
            symbols = dict(self.symbols); symbols[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check(symbols=symbols)

    def test_a_different_init_or_missing_marker_is_rejected(self):
        init = bytearray(self.init); init[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "differs"):
            self.check(init=init)
        self.image, self.elf, self.archive, self.init, self.symbols = linked_fixture(
            marker=b"x"*len(b"MAINLINE_STAGE0_USERSPACE_OK"))
        self.vmlinux.write_bytes(self.elf)
        with self.assertRaisesRegex(ValueError, "diagnostic executable"):
            self.check()

    def test_elf_symbol_range_must_be_unique_and_file_backed(self):
        self.assertEqual(elf_bytes_at(self.elf,self.symbols["__initramfs_start"],len(self.archive)),
                         self.archive)
        for address, size in ((-1,1),(0,0),(self.symbols["_end"]-1,1),(2**64-1,2)):
            with self.subTest(address=address,size=size), self.assertRaises(ValueError):
                elf_bytes_at(self.elf,address,size)
        elf = bytearray(self.elf); struct.pack_into("<H",elf,56,2); elf[120:176] = elf[64:120]
        with self.assertRaisesRegex(ValueError,"uniquely"):
            elf_bytes_at(elf,self.symbols["__initramfs_start"],len(self.archive))

    def test_nm_inventory_requires_all_symbols_without_duplicates(self):
        lines = [f"{self.symbols[name]:016x} T {name}" for name in sorted(REQUIRED_SYMBOLS)]
        with patch("scripts.validate_builtin.subprocess.check_output",return_value="\n".join(lines)):
            self.assertEqual(kernel_symbols(self.vmlinux),self.symbols)
        for output in ("\n".join(lines[:-1]),"\n".join(lines+[lines[0]])):
            with patch("scripts.validate_builtin.subprocess.check_output",return_value=output):
                with self.assertRaises(ValueError):
                    kernel_symbols(self.vmlinux)
