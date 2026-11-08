# SPDX-License-Identifier: GPL-2.0-only

import copy
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import unittest
from unittest.mock import patch

from scripts.fetch_kernel import check_archive_members, fetch_and_extract, load_lock
from tests.support import ROOT, temporary_directory


class SourceLockTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "sources.lock.json").read_text())

    def load_mutation(self, lock):
        with temporary_directory() as directory:
            path = Path(directory) / "sources.lock.json"
            path.write_text(json.dumps(lock))
            return load_lock(path)

    def test_repository_lock_is_fixed_upstream(self):
        self.assertEqual(load_lock(ROOT / "sources.lock.json"), self.lock)

    def test_device_forks_mutable_tags_and_unpinned_metadata(self):
        mutations = (("version", "master"), ("version", "7.4-rc1"), ("version", "7.3"),
                     ("tag", "master"), ("tag", "v7.3.2"),
                     ("archive_url", "https://example.org/linux-7.3.3.tar.xz"),
                     ("archive_url", self.lock["linux"]["archive_url"] + "?extra=1"),
                     ("git_url", "https://github.com/vendor/device-kernel.git"),
                     ("archive_sha256", "0" * 63), ("archive_sha256", "g" * 64),
                     ("commit", "HEAD"), ("commit", "0" * 41))
        for key, value in mutations:
            lock = copy.deepcopy(self.lock)
            lock["linux"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.load_mutation(lock)

    def test_malformed_lock_fields(self):
        for key in self.lock["linux"]:
            for value in (None, 123):
                lock = copy.deepcopy(self.lock)
                lock["linux"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.load_mutation(lock)

    def test_missing_field(self):
        lock = copy.deepcopy(self.lock)
        del lock["linux"]["archive_sha256"]
        with self.assertRaises(ValueError):
            self.load_mutation(lock)

    def test_format_and_schema_pin(self):
        for key, value in (("format", True), ("format", 2), ("linux", []),
                           ("dtschema_version", "latest"), ("dtschema_version", None)):
            lock = copy.deepcopy(self.lock)
            lock[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.load_mutation(lock)
        with self.assertRaises(ValueError):
            self.load_mutation([])


class PatchSeriesTests(unittest.TestCase):
    def test_series_paths_and_patch_syntax(self):
        names = [line for line in (ROOT / "patches/linux/series").read_text().splitlines()
                 if line and not line.startswith("#")]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("0003-exynos7570-stage0-clock-providers.patch", names)
        for index, name in enumerate(names, 1):
            with self.subTest(name=name):
                self.assertRegex(name, r"^[0-9]{4}-[a-z0-9-]+\.patch$")
                self.assertEqual(int(name[:4]), index)
                result = subprocess.run(["git", "apply", "--numstat",
                                         str(ROOT / "patches/linux" / name)],
                                        check=True, capture_output=True, text=True, timeout=5)
                self.assertTrue(result.stdout.strip())
                self.assertFalse(result.stderr)

    def test_only_the_local_clock_source_is_installed(self):
        source = ROOT / "kernel/drivers/clk/samsung/clk-exynos7570-stage0.c"
        self.assertTrue(source.is_file())
        build = (ROOT / "scripts/build.sh").read_text()
        self.assertRegex(build, re.compile(
            r'install -m 644 kernel/drivers/clk/samsung/clk-exynos7570-stage0\.c \\\n'
            r'\s+"\$LINUX/drivers/clk/samsung/"'))
        self.assertIn("CMU_MIF", source.read_text())


def synthetic_tar(members, mode="w"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for member in members:
            archive.addfile(member, io.BytesIO(b"x" * member.size) if member.isfile() else None)
    return buffer.getvalue()


class SourceArchiveTests(unittest.TestCase):
    def check(self, members):
        with tarfile.open(fileobj=io.BytesIO(synthetic_tar(members))) as archive:
            check_archive_members(archive, "linux-7.3.3")

    def test_relative_in_tree_files_and_links(self):
        regular = tarfile.TarInfo("linux-7.3.3/include/file.h")
        link = tarfile.TarInfo("linux-7.3.3/include/sub/link.h")
        link.type = tarfile.SYMTYPE
        link.linkname = "../file.h"
        self.check([regular, link])

    def test_paths_stay_in_exact_top_level(self):
        for name in ("/absolute", "../escape", "linux-7.3.3/../escape", "other/file"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check([tarfile.TarInfo(name)])

    def test_duplicate_paths(self):
        member = tarfile.TarInfo("linux-7.3.3/file")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.check([member, member])

    def test_special_files(self):
        for kind in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE):
            member = tarfile.TarInfo("linux-7.3.3/device")
            member.type = kind
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.check([member])

    def test_links_cannot_escape(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            for target in ("/etc/passwd", "../../outside", ""):
                link = tarfile.TarInfo("linux-7.3.3/sub/link")
                link.type, link.linkname = kind, target
                with self.subTest(kind=kind, target=target), self.assertRaises(ValueError):
                    self.check([link])


class FetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        member = tarfile.TarInfo("linux-7.3.3/synthetic-only")
        member.size = 1
        cls.archive = synthetic_tar([member], mode="w:xz")

    def setUp(self):
        self.lock = json.loads((ROOT / "sources.lock.json").read_text())
        # This mocked, tiny tar is explicitly not a real upstream archive.
        self.lock["linux"]["archive_sha256"] = hashlib.sha256(self.archive).hexdigest()

    def test_checksum_precedes_extraction(self):
        lock = copy.deepcopy(self.lock)
        lock["linux"]["archive_sha256"] = "0" * 64
        with temporary_directory() as directory:
            with patch("urllib.request.urlopen", return_value=io.BytesIO(self.archive)):
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    fetch_and_extract(lock, Path(directory))
            self.assertFalse((Path(directory) / "linux-7.3.3").exists())

    def test_mocked_download_and_verified_cached_archive(self):
        with temporary_directory() as directory:
            with patch("urllib.request.urlopen", return_value=io.BytesIO(self.archive)) as request:
                with patch("builtins.print"):
                    source = fetch_and_extract(self.lock, Path(directory))
            request.assert_called_once()
            self.assertEqual((source / "synthetic-only").read_bytes(), b"x")
            self.assertEqual((Path(directory) / "linux-7.3.3.tar.xz").read_bytes(), self.archive)

    def test_cached_checksum_is_rechecked(self):
        with temporary_directory() as directory:
            (Path(directory) / "linux-7.3.3.tar.xz").write_bytes(b"bad cached bytes")
            with patch("urllib.request.urlopen") as request:
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    fetch_and_extract(self.lock, Path(directory))
            request.assert_not_called()

    def test_existing_source_is_never_overwritten(self):
        with temporary_directory() as directory:
            root = Path(directory)
            (root / "linux-7.3.3.tar.xz").write_bytes(self.archive)
            (root / "linux-7.3.3").mkdir()
            marker = root / "linux-7.3.3/user-work"
            marker.write_text("keep")
            with patch("urllib.request.urlopen") as request:
                with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                    fetch_and_extract(self.lock, root)
            request.assert_not_called()
            self.assertEqual(marker.read_text(), "keep")

    def test_existing_partial_is_never_discarded(self):
        with temporary_directory() as directory:
            partial = Path(directory) / "linux-7.3.3.tar.xz.part"
            partial.write_text("keep")
            with patch("urllib.request.urlopen", return_value=io.BytesIO(self.archive)):
                with self.assertRaises(FileExistsError):
                    fetch_and_extract(self.lock, Path(directory))
            self.assertEqual(partial.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
