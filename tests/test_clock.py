# SPDX-License-Identifier: GPL-2.0-only
"""Compile the local clock driver against explicit host-only API stand-ins."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from tests.support import ROOT, temporary_directory


class ClockDriverTests(unittest.TestCase):
    def test_production_driver_with_mock_mmio_and_failure_injection(self):
        with temporary_directory() as directory:
            out = Path(directory)
            include = out / "include"
            include.mkdir()
            shutil.copyfile(ROOT / "tests/c/clock-kernel-mock.h",
                            include / "clock-kernel-mock.h")
            driver = ROOT / "kernel/drivers/clk/samsung/clk-exynos7570-stage0.c"
            for name in re.findall(r"^#include <(linux/[^>]+)>", driver.read_text(), re.MULTILINE):
                header = include / name
                header.parent.mkdir(parents=True, exist_ok=True)
                header.write_text('#include "clock-kernel-mock.h"\n')
            binary = out / "clock-stage0"
            compiler = os.environ.get("HOSTCC", "gcc")
            compiled = subprocess.run([
                compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g", "-O1",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                "-I", str(include), str(ROOT / "tests/c/clock-stage0.c"), "-o", str(binary)
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r"clock-stage0: [0-9]+ synthetic checks passed")
            self.assertIn("mocked CCF/MMIO only", result.stdout)
            self.assertFalse(result.stderr)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
