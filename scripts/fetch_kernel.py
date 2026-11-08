#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fetch checksum-pinned upstream Linux; never use a device kernel fork."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import tarfile
import urllib.request


def load_lock(path: Path) -> dict:
    lock = json.loads(path.read_text())
    if not isinstance(lock, dict) or type(lock.get("format")) is not int or lock["format"] != 1:
        raise ValueError("unsupported source-lock format")
    linux = lock.get("linux")
    required = ("version", "tag", "git_url", "commit", "archive_url", "archive_sha256")
    if not isinstance(linux, dict) or any(not isinstance(linux.get(key), str) for key in required):
        raise ValueError("source lock is missing required Linux string fields")
    version = linux["version"]
    if not re.fullmatch(r"[1-9][0-9]*\.[0-9]+\.[0-9]+", version):
        raise ValueError("source lock must name a fixed stable release")
    expected_url = ("https://cdn.kernel.org/pub/linux/kernel/"
                    f"v{version.split('.')[0]}.x/linux-{version}.tar.xz")
    if linux["archive_url"] != expected_url:
        raise ValueError("source archive is not the exact official kernel.org URL")
    if linux["git_url"] != "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git":
        raise ValueError("source repository is not upstream stable Linux")
    if linux["tag"] != f"v{version}":
        raise ValueError("source tag and version disagree")
    for key, length in (("archive_sha256", 64), ("commit", 40)):
        if not re.fullmatch(rf"[0-9a-f]{{{length}}}", linux[key]):
            raise ValueError(f"invalid pinned {key}")
    schema = lock.get("dtschema_version")
    if not isinstance(schema, str) or not re.fullmatch(r"20[0-9]{2}\.[0-9]{1,2}", schema):
        raise ValueError("dtschema must also have a fixed version")
    return lock


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_archive_members(archive: tarfile.TarFile, directory: str) -> None:
    seen = set()
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"unsafe source archive member: {member.name!r}")
        if path.parts[0] != directory:
            raise ValueError("source archive has an unexpected top-level directory")
        if str(path) in seen:
            raise ValueError("duplicate source archive member")
        seen.add(str(path))
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError("source archive contains a device or other special file")
        if member.issym() or member.islnk():
            target = PurePosixPath(member.linkname)
            if not member.linkname or target.is_absolute():
                raise ValueError("unsafe source archive link")
            if member.issym():
                target = path.parent / target
            target = PurePosixPath(posixpath.normpath(str(target)))
            if not target.parts or target.parts[0] != directory:
                raise ValueError("source archive link escapes its top-level directory")


def fetch_and_extract(lock: dict, work_root: Path) -> Path:
    linux = lock["linux"]
    directory = f"linux-{linux['version']}"
    work_root.mkdir(parents=True, exist_ok=True)
    archive_path = work_root / f"{directory}.tar.xz"
    if not archive_path.exists():
        partial = archive_path.with_suffix(".xz.part")
        # 'xb' avoids discarding a partial file that was not made by this run.
        request = urllib.request.Request(linux["archive_url"],
                                         headers={"User-Agent": "j5prime-mainline-stage0"})
        with urllib.request.urlopen(request, timeout=90) as response, partial.open("xb") as file:
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
        if sha256(partial) != linux["archive_sha256"]:
            raise ValueError("upstream archive SHA-256 mismatch; nothing extracted")
        partial.rename(archive_path)
    elif sha256(archive_path) != linux["archive_sha256"]:
        raise ValueError("existing source archive SHA-256 mismatch")

    source = work_root / directory
    if source.exists():
        raise ValueError("source directory already exists; refusing to overwrite it")
    with tarfile.open(archive_path, "r:xz") as archive:
        check_archive_members(archive, directory)
        archive.extractall(work_root, filter="data")
    print(f"Verified upstream Linux {linux['version']}: {linux['archive_sha256']}")
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("sources.lock.json"))
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()
    fetch_and_extract(load_lock(args.lock), args.work_root.resolve())


if __name__ == "__main__":
    main()
