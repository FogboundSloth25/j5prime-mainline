# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the production native gate with libfdt and explicit host stand-ins."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from tests.support import ROOT, temporary_directory


def write_mock_headers(include: Path, headers: list[str]) -> None:
    for name in headers:
        # glibc <errno.h> includes the system Linux UAPI error constants too.
        # Mocking linux/errno.h recursively suppresses EINVAL/ENOMEM in libc.
        if name == "linux/errno.h":
            continue
        header = include / name
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text('#include "framebuffer-kernel-mock.h"\n')


class FramebufferTests(unittest.TestCase):
    def test_production_gate_with_sanitizers_and_injected_failures(self):
        with temporary_directory() as directory:
            out = Path(directory)
            include = out / "include"
            include.mkdir()
            shutil.copyfile(ROOT / "tests/c/framebuffer-kernel-mock.h",
                            include / "framebuffer-kernel-mock.h")
            driver = ROOT / "kernel/drivers/video/fbdev/on5xelte-stage0-fb.c"
            renderer = ROOT / "kernel/drivers/video/fbdev/on5xelte-stage0-progress.c"
            headers = re.findall(r"^#include <((?:linux|asm)/[^>]+)>",
                                 driver.read_text() + "\n" + renderer.read_text(), re.MULTILINE)
            write_mock_headers(include, headers)
            binary = out / "framebuffer-stage0"
            compiled = subprocess.run([
                os.environ.get("HOSTCC", "gcc"), "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g", "-O1",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                "-I", str(include), str(ROOT / "tests/c/framebuffer-stage0.c"), "-lfdt", "-o", str(binary)
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r"framebuffer-stage0: [0-9]+ synthetic checks passed")
            self.assertIn("mocked MMIO/memblock/platform only; no hardware evidence", result.stdout)
            self.assertFalse(result.stderr)
            print(result.stdout.strip())

    def test_no_mmio_write_or_display_reprogramming_api(self):
        source = (ROOT / "kernel/drivers/video/fbdev/on5xelte-stage0-fb.c").read_text()
        self.assertNotRegex(source, r"\b(?:writel|writew|writeb|memcpy_toio|memset_io|regmap_write)\s*\(")
        self.assertIn("memblock_reserve(candidate.address, candidate.protected_bytes)", source)
        self.assertIn("device_initcall_sync(on5xelte_stage0_fb_register)", source)

    def test_renderer_is_memory_only_and_original_gates_remain(self):
        source = (ROOT / "kernel/drivers/video/fbdev/on5xelte-stage0-progress.c").read_text()
        self.assertNotRegex(source, r"\b(?:readl|writel|writew|writeb|regmap_write)\s*\(")
        self.assertNotIn("0x14830000", source)
        self.assertIn("memcpy_toio", source)
        self.assertIn("mapped_bytes > protected_bytes", source)
        gate = (ROOT / "kernel/drivers/video/fbdev/on5xelte-stage0-fb.c").read_text()
        checkpoint = gate.split("void __init on5xelte_stage0_fb_checkpoint(", 1)[1].split("static int __init on5xelte_stage0_fb_register", 1)[0]
        self.assertLess(checkpoint.index("validate_fdt("), checkpoint.index("early_ioremap("))
        self.assertLess(checkpoint.index("stable_snapshot("), checkpoint.index("on5xelte_stage0_progress_draw("))
        self.assertIn("prepared = false", checkpoint)
        self.assertIn("kernel_first != STAGE0_RAM_BASE", gate)

    def test_system_errno_header_is_not_shadowed_by_standins(self):
        with temporary_directory() as directory:
            include = Path(directory) / "include"
            write_mock_headers(include, ["linux/errno.h", "linux/init.h"])
            self.assertFalse((include / "linux/errno.h").exists())
            self.assertTrue((include / "linux/init.h").is_file())
