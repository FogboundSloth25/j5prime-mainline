# SPDX-License-Identifier: GPL-2.0-only
"""Native contracts are distinct from preserved loader-address history, CI only."""
import json
import re
import struct
import unittest
from scripts.native_boot import BOOTARGS
from scripts.validate import (NATIVE_BOOTARGS, NATIVE_ENTRY, NATIVE_INITRAMFS_SOURCE,
                              NATIVE_KERNEL_LIMIT, PAYLOAD_ENTRY, TIMA_START,
                              parse_config, validate_config, validate_image)
from tests.support import ROOT, synthetic_image
from tests.test_validate import POWER_SUPPLY_KCONFIG


class NativeContractTests(unittest.TestCase):
    def test_explicit_native_and_legacy_memory_contracts(self):
        native = validate_image(synthetic_image(),load_address=NATIVE_ENTRY)
        self.assertEqual(native["planned_load_address"],0x40000000)
        self.assertEqual(native["planned_memory_limit"],0x42000000)
        self.assertEqual(native["load_contract"],"native-sboot-stage0")
        self.assertFalse(native["firmware_acceptance_and_actual_placement_verified"])
        legacy = validate_image(synthetic_image())
        self.assertEqual(legacy["planned_load_address"],PAYLOAD_ENTRY)
        self.assertEqual(legacy["planned_memory_limit"],TIMA_START)
        self.assertEqual(legacy["load_contract"],"legacy-loader-stage0")

    def test_native_full_memory_including_bss_must_fit_before_compatibility_ramdisk(self):
        image = synthetic_image(); struct.pack_into("<Q",image,16,NATIVE_KERNEL_LIMIT-NATIVE_ENTRY)
        self.assertEqual(validate_image(image,load_address=NATIVE_ENTRY)["planned_memory_end"],NATIVE_KERNEL_LIMIT)
        struct.pack_into("<Q",image,16,NATIVE_KERNEL_LIMIT-NATIVE_ENTRY+1)
        with self.assertRaisesRegex(ValueError,"native"):
            validate_image(image,load_address=NATIVE_ENTRY)

    def test_old_smoke_address_and_other_placements_are_not_native_proof(self):
        for address in (0x40080000,0x40000001,0x42000000,0,2**64,True):
            with self.subTest(address=address),self.assertRaisesRegex(ValueError,"load address"):
                validate_image(synthetic_image(),load_address=address)
        image = synthetic_image(); struct.pack_into("<Q",image,24,2)
        with self.assertRaisesRegex(ValueError,"flags"):
            validate_image(image,load_address=NATIVE_ENTRY)

    def test_one_forced_cmdline_in_config_dts_and_boot_header(self):
        fragment = (ROOT/"configs/on5xelte-stage0.config").read_text()
        config = parse_config(fragment)
        self.assertEqual(config["CONFIG_CMDLINE"],json.dumps(NATIVE_BOOTARGS))
        self.assertEqual(config["CONFIG_CMDLINE_FORCE"],"y")
        self.assertEqual(config["CONFIG_INITRAMFS_SOURCE"],json.dumps(NATIVE_INITRAMFS_SOURCE))
        self.assertEqual(config["CONFIG_FB_ON5XELTE_STAGE0"],"y")
        dts = (ROOT/"dts/exynos7570-on5xelte-stage0.dts").read_text()
        self.assertEqual(re.search(r'bootargs = "([^"]+)";',dts)[1],NATIVE_BOOTARGS)
        self.assertEqual(BOOTARGS,NATIVE_BOOTARGS)
        self.assertEqual(NATIVE_BOOTARGS.split().count("mem=512M"),1)
        self.assertIn('status = "disabled";',dts)

    def test_native_gate_is_required_even_if_the_fragment_is_weakened(self):
        fragment = (ROOT/"configs/on5xelte-stage0.config").read_text()
        hidden = "\n".join(f"CONFIG_{name}=y" for name in (
            "ARM_GIC","ARM_PSCI_FW","ARM_ARCH_TIMER","CLKSRC_EXYNOS_MCT",
            "COMMON_CLK","COMMON_CLK_SAMSUNG"))
        for key in ("FB_ON5XELTE_STAGE0","INITRAMFS_SOURCE","CMDLINE_FORCE"):
            weakened = re.sub(rf'^CONFIG_{key}=.*$',"",fragment,flags=re.MULTILINE)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,f"CONFIG_{key}"):
                validate_config(weakened+"\n"+hidden,weakened,POWER_SUPPLY_KCONFIG)

    def test_build_calls_actual_linkage_and_native_boot_validators(self):
        source = (ROOT/"scripts/build.sh").read_text()
        self.assertIn('"$KERNEL_OUT/on5xelte-stage0-initramfs.cpio.gz"',source)
        self.assertIn('--load-address 0x40000000',source)
        self.assertIn('python3 -m scripts.validate_builtin',source)
        self.assertIn('python3 -m scripts.native_boot pack',source)
        self.assertIn('python3 -m scripts.native_boot verify',source)
