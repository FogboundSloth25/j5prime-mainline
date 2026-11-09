# SPDX-License-Identifier: GPL-2.0-only

import os
import struct
import subprocess
import sys
import unittest

from scripts.validate import (PAYLOAD_ENTRY, TIMA_START, parse_config,
                              validate_config, validate_image, validate_init_elf)
from tests.support import ROOT, synthetic_elf, synthetic_image


POWER_SUPPLY_KCONFIG = """# Synthetic registry, not an upstream Kconfig copy.
menuconfig POWER_SUPPLY
    bool "Synthetic class"
if POWER_SUPPLY
config POWER_SUPPLY_DEBUG
    bool
config POWER_SUPPLY_HWMON
    bool
config BATTERY_FIXTURE
    tristate
config CHARGER_FIXTURE
    tristate
config SUPPLY_FIXTURE_HELPER
    tristate
endif
"""


class ImageTests(unittest.TestCase):
    def test_non_efi_header_branch_pc_is_byte_four(self):
        report = validate_image(synthetic_image())
        self.assertEqual(report["entry_branch_offset"], 64)
        self.assertEqual(report["entry_instruction_offset"], 4)
        self.assertEqual(report["planned_load_address"], PAYLOAD_ENTRY)
        self.assertFalse(report["hardware_boot_tested"])
        self.assertFalse(report["flashable_android_image"])

    def test_truncated_header(self):
        for length in (0, 4, 63):
            with self.subTest(length=length), self.assertRaises(ValueError):
                validate_image(synthetic_image()[:length])

    def test_magic(self):
        image = synthetic_image()
        struct.pack_into("<I", image, 56, 0)
        with self.assertRaisesRegex(ValueError, "ARM64"):
            validate_image(image)

    def test_flags(self):
        for flags in (0, 1, 4, 6, 0xB, 0x1A):
            image = synthetic_image()
            struct.pack_into("<Q", image, 24, flags)
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                validate_image(image)

    def test_efi_and_unrecognized_entry_instructions(self):
        for offset, word in ((0, 0xFA405A4D), (0, 0x14000010), (4, 0xD503201F)):
            image = synthetic_image()
            struct.pack_into("<I", image, offset, word)
            with self.subTest(offset=offset, word=word), self.assertRaises(ValueError):
                validate_image(image)

    def test_branch_bounds_and_sign_extension(self):
        for target in (-4, 0, 4, 60, 512, 1024):
            image = synthetic_image()
            struct.pack_into("<I", image, 4, 0x14000000 | (((target - 4) // 4) & 0x3FFFFFF))
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "branch"):
                validate_image(image)

    def test_nonzero_text_offset(self):
        image = synthetic_image()
        struct.pack_into("<Q", image, 8, 0x80000)
        with self.assertRaisesRegex(ValueError, "offset/alignment"):
            validate_image(image)

    def test_required_memory_must_include_file(self):
        for size in (0, 511):
            image = synthetic_image()
            struct.pack_into("<Q", image, 16, size)
            with self.subTest(size=size), self.assertRaises(ValueError):
                validate_image(image)

    def test_exact_reservation_boundary_is_allowed(self):
        image = synthetic_image()
        struct.pack_into("<Q", image, 16, TIMA_START - PAYLOAD_ENTRY)
        self.assertEqual(validate_image(image)["planned_memory_end"], TIMA_START)

    def test_bss_memory_cannot_overlap_tima(self):
        image = synthetic_image()
        struct.pack_into("<Q", image, 16, TIMA_START - PAYLOAD_ENTRY + 1)
        with self.assertRaisesRegex(ValueError, "TIMA"):
            validate_image(image)

    def test_reserved_header_words(self):
        for offset in (32, 40, 48, 60):
            image = synthetic_image()
            image[offset] = 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_image(image)


class ElfTests(unittest.TestCase):
    def test_static_aarch64(self):
        report = validate_init_elf(synthetic_elf())
        self.assertTrue(report["static"])
        self.assertEqual(report["architecture"], "aarch64")
        self.assertEqual(report["entry_address"], 0x400100)

    def test_invalid_identification(self):
        for elf in (b"", synthetic_elf()[:63], b"not ELF" + b"\0" * 100):
            with self.subTest(length=len(elf)), self.assertRaises(ValueError):
                validate_init_elf(elf)
        for offset, value in ((4, 1), (5, 2), (6, 0)):
            elf = synthetic_elf()
            elf[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_init_elf(elf)

    def test_host_architecture_or_pie(self):
        for offset, value in ((16, 3), (18, 62)):
            elf = synthetic_elf()
            struct.pack_into("<H", elf, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_init_elf(elf)

    def test_elf_version_and_header_size(self):
        for offset, fmt, value in ((20, "I", 0), (52, "H", 0)):
            elf = synthetic_elf()
            struct.pack_into("<" + fmt, elf, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_init_elf(elf)

    def test_program_header_bounds(self):
        for offset, fmt, value in ((32, "Q", 0), (32, "Q", 500),
                                   (54, "H", 55), (56, "H", 0), (56, "H", 100)):
            elf = synthetic_elf()
            struct.pack_into("<" + fmt, elf, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                validate_init_elf(elf)

    def test_dynamic_loading_is_rejected(self):
        for segment_type in (2, 3):
            elf = synthetic_elf()
            struct.pack_into("<I", elf, 64, segment_type)
            with self.subTest(segment_type=segment_type), self.assertRaisesRegex(ValueError, "dynamic"):
                validate_init_elf(elf)

    def test_load_segment_is_required(self):
        elf = synthetic_elf()
        struct.pack_into("<I", elf, 64, 4)
        with self.assertRaisesRegex(ValueError, "no loadable"):
            validate_init_elf(elf)

    def test_segment_file_bounds(self):
        elf = synthetic_elf()
        struct.pack_into("<Q", elf, 72, 500)
        with self.assertRaisesRegex(ValueError, "file bounds"):
            validate_init_elf(elf)

    def test_invalid_load_segments(self):
        for offset, fmt, value in ((104, "Q", 500), (68, "I", 8),
                                   (112, "Q", 3), (80, "Q", 0x400010),
                                   (80, "Q", 2**64 - 100)):
            elf = synthetic_elf()
            struct.pack_into("<" + fmt, elf, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                validate_init_elf(elf)

    def test_entry_must_be_file_backed_and_executable(self):
        for offset, fmt, value in ((24, "Q", 0x400300), (68, "I", 4)):
            elf = synthetic_elf()
            struct.pack_into("<" + fmt, elf, offset, value)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "entry"):
                validate_init_elf(elf)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.fragment = (ROOT / "configs/on5xelte-stage0.config").read_text()
        self.core = "\n".join(f"{key}=y" for key in (
            "CONFIG_ARM_GIC", "CONFIG_ARM_PSCI_FW", "CONFIG_ARM_ARCH_TIMER",
            "CONFIG_CLKSRC_EXYNOS_MCT", "CONFIG_COMMON_CLK",
            "CONFIG_COMMON_CLK_SAMSUNG"))
        self.actual = self.fragment + "\n" + self.core + "\n"

    def test_strict_fragment_and_core_selection(self):
        report = validate_config(self.actual, self.fragment, POWER_SUPPLY_KCONFIG)
        self.assertGreater(report["requested_options_checked"], 50)
        self.assertEqual(report["core_platform_checks"], 8)
        self.assertTrue(report["stage0_clock_provider_built_in"])
        self.assertFalse(report["firmware_mif_clock_path_managed"])
        self.assertEqual(report["power_supply_options_checked"], 5)
        self.assertFalse(report["power_supply_optional_support_enabled"])

    def test_quote_and_disabled_value_parsing(self):
        config = parse_config('CONFIG_TEXT="has spaces"\n# CONFIG_NET is not set\n')
        self.assertEqual(config, {"CONFIG_TEXT": '"has spaces"', "CONFIG_NET": "n"})

    def test_conflicting_duplicates(self):
        with self.assertRaisesRegex(ValueError, "conflicting"):
            parse_config("CONFIG_FB=y\n# CONFIG_FB is not set\n")

    def test_same_duplicate_is_consistent(self):
        self.assertEqual(parse_config("CONFIG_FB=y\nCONFIG_FB=y"), {"CONFIG_FB": "y"})

    def test_requested_enabled_option_cannot_disappear(self):
        with self.assertRaisesRegex(ValueError, "CONFIG_FB_SIMPLE"):
            validate_config(self.actual.replace("CONFIG_FB_SIMPLE=y", ""), self.fragment,
                            POWER_SUPPLY_KCONFIG)

    def test_requested_disabled_option_cannot_be_enabled(self):
        with self.assertRaisesRegex(ValueError, "CONFIG_MMC"):
            validate_config(self.actual.replace("# CONFIG_MMC is not set", "CONFIG_MMC=y"),
                            self.fragment, POWER_SUPPLY_KCONFIG)

    def test_absent_disabled_option_is_off(self):
        actual = self.actual.replace("# CONFIG_MMC is not set", "")
        validate_config(actual, self.fragment, POWER_SUPPLY_KCONFIG)

    def test_hidden_core_support_is_not_assumed(self):
        with self.assertRaisesRegex(ValueError, "CONFIG_CLKSRC_EXYNOS_MCT"):
            validate_config(self.actual.replace("CONFIG_CLKSRC_EXYNOS_MCT=y", ""),
                             self.fragment, POWER_SUPPLY_KCONFIG)

    def test_stage0_clock_provider_is_requested_and_required(self):
        self.assertEqual(parse_config(self.fragment)["CONFIG_EXYNOS_7570_STAGE0_CLK"], "y")
        for symbol in ("EXYNOS_7570_STAGE0_CLK", "COMMON_CLK_SAMSUNG"):
            for value in ("", f"CONFIG_{symbol}=m", f"# CONFIG_{symbol} is not set"):
                actual = self.actual.replace(f"CONFIG_{symbol}=y", value)
                with self.subTest(symbol=symbol, value=value), self.assertRaisesRegex(
                        ValueError, f"CONFIG_{symbol}"):
                    validate_config(actual, self.fragment, POWER_SUPPLY_KCONFIG)

    def test_command_line_must_survive_exactly(self):
        with self.assertRaisesRegex(ValueError, "CONFIG_CMDLINE"):
            validate_config(self.actual.replace("maxcpus=1", "maxcpus=4"), self.fragment,
                            POWER_SUPPLY_KCONFIG)

    def test_empty_fragment_is_not_a_validation(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            validate_config(self.core, "# empty\n", POWER_SUPPLY_KCONFIG)

    def test_mandatory_supply_class_is_requested_and_checked(self):
        self.assertEqual(parse_config(self.fragment)["CONFIG_POWER_SUPPLY"], "y")
        with self.assertRaisesRegex(ValueError, "CONFIG_POWER_SUPPLY"):
            validate_config(self.actual.replace("CONFIG_POWER_SUPPLY=y", ""),
                            self.fragment, POWER_SUPPLY_KCONFIG)

    def test_supply_optional_support_cannot_be_builtin_or_module(self):
        for symbol in ("POWER_SUPPLY_DEBUG", "POWER_SUPPLY_HWMON", "BATTERY_FIXTURE",
                       "CHARGER_FIXTURE", "SUPPLY_FIXTURE_HELPER"):
            for value in ("y", "m"):
                actual = self.actual + f"CONFIG_{symbol}={value}\n"
                with self.subTest(symbol=symbol, value=value), self.assertRaisesRegex(
                        ValueError, f"CONFIG_{symbol}: optional power-supply"):
                    validate_config(actual, self.fragment, POWER_SUPPLY_KCONFIG)

    def test_explicitly_disabled_supply_option_is_allowed(self):
        actual = self.actual + "# CONFIG_BATTERY_FIXTURE is not set\n"
        validate_config(actual, self.fragment, POWER_SUPPLY_KCONFIG)

    def test_indented_supply_option_is_not_ignored(self):
        inventory = POWER_SUPPLY_KCONFIG.replace("config SUPPLY_FIXTURE_HELPER",
                                                 "\tconfig SUPPLY_FIXTURE_HELPER")
        actual = self.actual + "CONFIG_SUPPLY_FIXTURE_HELPER=y\n"
        with self.assertRaisesRegex(ValueError, "CONFIG_SUPPLY_FIXTURE_HELPER"):
            validate_config(actual, self.fragment, inventory)

    def test_incomplete_supply_inventory_is_not_accepted(self):
        for inventory in ("", "config POWER_SUPPLY\n", "config BATTERY_FIXTURE\n",
                          POWER_SUPPLY_KCONFIG + "config BATTERY_FIXTURE\n"):
            with self.subTest(inventory=inventory), self.assertRaisesRegex(
                    ValueError, "Kconfig inventory"):
                validate_config(self.actual, self.fragment, inventory)

    def test_split_supply_inventory_is_not_silently_ignored(self):
        for directive in ("source", "rsource", "osource", "orsource"):
            inventory = POWER_SUPPLY_KCONFIG + f'{directive} "another/Kconfig"\n'
            with self.subTest(directive=directive), self.assertRaisesRegex(
                    ValueError, "unsupported includes"):
                validate_config(self.actual, self.fragment, inventory)

    def test_config_cli_requires_supply_inventory(self):
        result = subprocess.run([sys.executable, "-m", "scripts.validate", "config",
                                 "not-read.config", "not-read.fragment"], cwd=ROOT,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires the upstream power-supply Kconfig inventory", result.stderr)


class LocalBuildGuardTests(unittest.TestCase):
    def test_non_ci_execution_stops_before_any_compilation(self):
        env = dict(os.environ)
        env.pop("GITHUB_ACTIONS", None)
        result = subprocess.run(["bash", "scripts/build.sh"], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn("compilation is CI-only", result.stderr)

    def test_build_shell_syntax(self):
        subprocess.run(["bash", "-n", "scripts/build.sh"], cwd=ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
