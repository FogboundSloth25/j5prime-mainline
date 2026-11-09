#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""CI-only recipient-encrypted outputs, including failure diagnostics; no fallback."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile

RECIPIENT_CERT_SHA256 = "63899d32ecbbffb4486ad066e69aaf0753a10da4504938ef8279dd90f9e905e0"
INPUT_DIRECTORIES = ("dist", "out/logs", "out/reports", "out/source")
SCHEME = "CMS AES-256-GCM; RSA-OAEP SHA-256"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def gather_files(root: Path) -> list[Path]:
    files = []
    for directory in INPUT_DIRECTORIES:
        top = root / directory
        if not top.exists() and not top.is_symlink():
            continue
        for path in [top, *sorted(top.rglob("*"))]:
            relative = path.relative_to(root)
            if (any(part.startswith(".") for part in relative.parts) or
                    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", relative.as_posix())):
                raise ValueError("unsafe artifact filename; encryption stopped")
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
                raise ValueError("nonregular/symlink/hardlink artifact input; encryption stopped")
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def encrypt_bundle(root: Path, certificate: str, output: Path, metadata: dict, *,
                   expected_certificate_sha256: str = RECIPIENT_CERT_SHA256,
                   work_root: Path | None = None) -> dict:
    # The production CLI cannot override the pinned recipient. Test callers use
    # their own explicitly synthetic certificate, never the real private key.
    actual_certificate_hash = hashlib.sha256(certificate.encode()).hexdigest()
    if actual_certificate_hash != expected_certificate_sha256:
        raise ValueError("recipient certificate mismatch; no plaintext fallback")
    files = gather_files(root)
    if output.exists() and any(output.iterdir()):
        raise ValueError("encrypted output directory is not empty")
    manifest = {"schema": 1, "context": metadata, "files": {}}
    for path in files:
        manifest["files"][path.relative_to(root).as_posix()] = {
            "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    run_id, attempt = metadata["run_id"], metadata["run_attempt"]
    if not str(run_id).isdigit() or not str(attempt).isdigit():
        raise ValueError("invalid CI run identity")
    name = f"on5xelte-native-stage0-{run_id}-{attempt}.tar.gz.cms"
    with tempfile.TemporaryDirectory(prefix="native-stage0-encryption-", dir=work_root) as directory:
        private = Path(directory)
        cert = private / "recipient.cert.pem"
        cert.write_text(certificate); cert.chmod(0o600)
        subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "0"],
                       check=True, capture_output=True)
        bundle = private / "outputs.tar.gz"
        with bundle.open("xb") as raw:
            with gzip.GzipFile(fileobj=raw, filename="", mode="wb", compresslevel=1,
                               mtime=int(metadata.get("source_date_epoch", 0))) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    info = tarfile.TarInfo("bundle-manifest.json")
                    info.size, info.mode = len(manifest_bytes), 0o600
                    archive.addfile(info, io.BytesIO(manifest_bytes))
                    for path in files:
                        relative = path.relative_to(root).as_posix()
                        info = tarfile.TarInfo(relative)
                        info.size, info.mode = manifest["files"][relative]["bytes"], 0o600
                        info.mtime = int(metadata.get("source_date_epoch", 0))
                        with path.open("rb") as file:
                            archive.addfile(info, file)
                        if sha256_file(path) != manifest["files"][relative]["sha256"]:
                            raise ValueError("artifact changed during bundling; encryption stopped")
        bundle.chmod(0o600)
        encrypted = private / name
        subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm",
                        "-in", str(bundle), "-out", str(encrypted), "-outform", "DER",
                        "-recip", str(cert), "-keyopt", "rsa_padding_mode:oaep",
                        "-keyopt", "rsa_oaep_md:sha256"], check=True, capture_output=True)
        if not encrypted.is_file() or not encrypted.stat().st_size:
            raise ValueError("empty CMS result; no plaintext fallback")
        receipt = {"schema": 1, "context": metadata, "scheme": SCHEME,
                   "recipient_certificate_sha256": actual_certificate_hash,
                   "ciphertext_file": name, "ciphertext_bytes": encrypted.stat().st_size,
                   "ciphertext_sha256": sha256_file(encrypted),
                   "raw_images_or_log_bundles_publicly_uploaded": False,
                   "phone_write_authorized": False}
        output.mkdir(parents=True, exist_ok=True)
        with (output / name).open("xb") as destination:
            with encrypted.open("rb") as source:
                shutil.copyfileobj(source, destination)
        with (output / "encrypted-receipt.json").open("x") as file:
            file.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Artifact export is CI-only; use the GitHub workflow.")
    os.umask(0o077)
    root = Path.cwd()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if commit != os.environ["GITHUB_SHA"]:
        raise SystemExit("source/CI commit mismatch; no artifact export")
    epoch = int(subprocess.check_output(["git", "log", "-1", "--format=%ct"], text=True))
    metadata = {"repository": os.environ["GITHUB_REPOSITORY"], "repository_commit": commit,
                "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                "build_job_status": os.environ["BUILD_JOB_STATUS"], "source_date_epoch": epoch}
    receipt = encrypt_bundle(root, os.environ.get("STAGE0_BUILD_RECIPIENT_CERT", ""),
                             root / "out/encrypted", metadata, work_root=Path(os.environ["RUNNER_TEMP"]))
    print(f"Encrypted CI output: {receipt['ciphertext_file']} ({receipt['ciphertext_bytes']} bytes).")
    print("Raw images and diagnostic/source bundles are not uploaded; no phone write authorized.")


if __name__ == "__main__":
    main()
