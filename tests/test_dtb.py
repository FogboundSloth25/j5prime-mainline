# SPDX-License-Identifier: GPL-2.0-only
"""Compile our DTS and mutate host-only fixtures; no device dumps or kernel build."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest

from scripts.validate import validate_dtb
from tests.support import ROOT, temporary_directory

OSC = "/clock-osc"
MIF = "/soc/clock-controller@10460000"
PERI = "/soc/clock-controller@101f0000"
MCT = "/soc/timer@101b0000"

# A tiny, independently written test preprocessor fixture. CI builds the real
# DTB using the interrupt binding headers from the pinned upstream archive.
INTERRUPT_FIXTURE = """#define GIC_SPI 0
#define GIC_PPI 1
#define GIC_CPU_MASK_SIMPLE(n) (((1 << (n)) - 1) << 8)
#define IRQ_TYPE_LEVEL_HIGH 4
#define IRQ_TYPE_LEVEL_LOW 8
"""


class DtbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = temporary_directory()
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        include = root / "dt-bindings/interrupt-controller"
        include.mkdir(parents=True)
        (include / "arm-gic.h").write_text(INTERRUPT_FIXTURE)
        source = subprocess.run([
            os.environ.get("HOSTCC", "cc"), "-E", "-P", "-nostdinc", "-undef",
            "-D__DTS__", "-x", "assembler-with-cpp", "-I", str(ROOT / "dts"),
            "-I", str(root), str(ROOT / "dts/exynos7570-on5xelte-stage0.dts")
        ], check=True, capture_output=True, text=True, timeout=30)
        if source.stderr:
            raise ValueError("DTS preprocessor diagnostics: " + source.stderr)
        cls.template = root / "stage0.dtb"
        result = subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(cls.template)],
                                input=source.stdout, check=True, capture_output=True,
                                text=True, timeout=30)
        if result.stderr:
            raise ValueError("DTS compiler diagnostics: " + result.stderr)

    def setUp(self):
        self.directory = temporary_directory()
        self.addCleanup(self.directory.cleanup)
        self.dtb = Path(self.directory.name) / "mutated.dtb"
        self.restore()

    def restore(self):
        shutil.copyfile(self.template, self.dtb)

    def put(self, node, prop, values, kind="x"):
        words = [f"{value:x}" for value in values] if kind == "x" else values
        subprocess.run(["fdtput", "-t", kind, str(self.dtb), node, prop, *words],
                       check=True, capture_output=True, timeout=5)

    def get(self, node, prop):
        result = subprocess.run(["fdtget", "-t", "x", str(self.dtb), node, prop],
                                check=True, capture_output=True, text=True, timeout=5)
        return [int(word, 16) for word in result.stdout.split()]

    def delete(self, node, prop):
        subprocess.run(["fdtput", "-d", str(self.dtb), node, prop],
                       check=True, capture_output=True, timeout=5)

    def test_source_dtb_and_distinct_mct_clocks(self):
        report = validate_dtb(self.dtb)
        self.assertEqual(report["clock_relationship_checks"], 3)
        self.assertTrue(report["mct_tick_uses_oscillator"])
        self.assertTrue(report["mct_pclk_uses_managed_gate"])
        self.assertFalse(report["firmware_mif_clock_path_managed"])
        self.assertFalse(report["hardware_boot_tested"])
        self.assertFalse(report["framebuffer_enabled"])

    def test_legal_phandle_renumbering_is_not_a_topology_change(self):
        for node, handle in ((OSC, 0x101), (MIF, 0x202), (PERI, 0x303)):
            self.put(node, "phandle", [handle])
        self.put(MIF, "clocks", [0x101])
        self.put(PERI, "clocks", [0x202])
        self.put(MCT, "clocks", [0x101, 0x303])
        validate_dtb(self.dtb)

    def test_mct_cannot_swap_or_reuse_inert_clock_handles(self):
        osc, mif, peri = (self.get(node, "phandle")[0] for node in (OSC, MIF, PERI))
        for values in ([peri, osc], [osc, osc], [osc, mif], [peri, peri],
                       [0, peri], [osc, 0xABCDEF], [osc], [osc, peri, 0]):
            with self.subTest(values=values):
                self.restore()
                self.put(MCT, "clocks", values)
                with self.assertRaisesRegex(ValueError, "clock relationship"):
                    validate_dtb(self.dtb)

    def test_parent_cycles_wrong_sources_and_extra_specifier_cells(self):
        osc, mif, peri = (self.get(node, "phandle")[0] for node in (OSC, MIF, PERI))
        mutations = ((MIF, [mif]), (MIF, [peri]), (MIF, [0xABCDEF]),
                     (MIF, [osc, 0]), (PERI, [osc]), (PERI, [peri]),
                     (PERI, [mif, 0]))
        for node, values in mutations:
            with self.subTest(node=node, values=values):
                self.restore()
                self.put(node, "clocks", values)
                with self.assertRaisesRegex(ValueError, "clock relationship"):
                    validate_dtb(self.dtb)

    def test_clock_provider_properties_are_checked(self):
        mutations = (
            (OSC, "compatible", ["fixed-factor-clock"], "s"),
            (OSC, "clock-frequency", [24000000], "x"),
            (OSC, "#clock-cells", [1], "x"),
            (MIF, "compatible", ["samsung,exynos7570-cmu-peri-stage0"], "s"),
            (MIF, "reg", [0, 0x10460000, 0, 0x2000], "x"),
            (MIF, "#clock-cells", [1], "x"),
            (MIF, "clock-names", ["peri-bus"], "s"),
            (PERI, "compatible", ["samsung,exynos7570-cmu-mif-stage0"], "s"),
            (PERI, "reg", [0, 0x101F0010, 0, 0x1000], "x"),
            (PERI, "#clock-cells", [1], "x"),
            (PERI, "clock-names", ["oscclk"], "s"),
            (MCT, "clock-names", ["mct", "fin_pll"], "s"),
        )
        for node, prop, values, kind in mutations:
            with self.subTest(node=node, prop=prop):
                self.restore()
                self.put(node, prop, values, kind)
                with self.assertRaisesRegex(ValueError, "DTB mismatch"):
                    validate_dtb(self.dtb)

    def test_missing_clock_properties_fail_closed(self):
        for node, prop in ((OSC, "phandle"), (MIF, "phandle"), (PERI, "phandle"),
                           (MIF, "clocks"), (PERI, "clocks"), (MCT, "clocks"),
                           (PERI, "clock-names")):
            with self.subTest(node=node, prop=prop):
                self.restore()
                self.delete(node, prop)
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    validate_dtb(self.dtb)

    def test_illegal_or_duplicate_clock_phandles_fail_closed(self):
        osc = self.get(OSC, "phandle")[0]
        for node, values in ((OSC, [0]), (MIF, [0xFFFFFFFF]), (PERI, [osc]),
                             (PERI, [1, 2])):
            with self.subTest(node=node, values=values):
                self.restore()
                self.put(node, "phandle", values)
                with self.assertRaisesRegex(ValueError, "phandle"):
                    validate_dtb(self.dtb)

    def test_disabled_clock_providers_are_not_usable(self):
        for node in (OSC, MIF, PERI):
            for status in ("disabled", "reserved", "fail"):
                with self.subTest(node=node, status=status):
                    self.restore()
                    self.put(node, "status", [status], "s")
                    with self.assertRaisesRegex(ValueError, "provider is unavailable"):
                        validate_dtb(self.dtb)

    def test_explicitly_available_clock_provider_is_allowed(self):
        for node in (OSC, MIF, PERI):
            self.put(node, "status", ["okay"], "s")
        validate_dtb(self.dtb)

    def test_no_guessed_fixed_peri_bus_frequency(self):
        for node in (MIF, PERI):
            with self.subTest(node=node):
                self.restore()
                self.put(node, "clock-frequency", [26000000])
                with self.assertRaisesRegex(ValueError, "fixed PERI"):
                    validate_dtb(self.dtb)


if __name__ == "__main__":
    unittest.main()
