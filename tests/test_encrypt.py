# SPDX-License-Identifier: GPL-2.0-only
"""Recipient encryption round trip with a synthetic CI key, never the real key."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest
from scripts.encrypt_artifacts import encrypt_bundle, gather_files
from tests.support import ROOT, temporary_directory


class EncryptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key_directory = temporary_directory(); cls.addClassCleanup(cls.key_directory.cleanup)
        root = Path(cls.key_directory.name)
        cls.key, cls.cert = root/"synthetic.key.pem", root/"synthetic.cert.pem"
        subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes",
                        "-keyout",str(cls.key),"-out",str(cls.cert),"-days","1",
                        "-subj","/CN=synthetic-CI-only"],check=True,capture_output=True,timeout=30)
        cls.certificate = cls.cert.read_text()
        cls.certificate_hash = hashlib.sha256(cls.certificate.encode()).hexdigest()

    def setUp(self):
        directory = temporary_directory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for relative, data in (("dist/Image",b"synthetic image"),
                               ("out/logs/failure.log",b"synthetic failure log"),
                               ("out/source/port-source.tar.gz",b"synthetic source")):
            path = self.root/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        (self.root/"not-allowed-phone-fixture.img").write_bytes(b"not included")
        self.metadata = {"repository":"synthetic/fixture","repository_commit":"a"*40,
                         "run_id":"1","run_attempt":"1","build_job_status":"failure","source_date_epoch":0}
        self.output = self.root/"out/encrypted"

    def export(self, certificate=None):
        return encrypt_bundle(self.root,self.certificate if certificate is None else certificate,
                              self.output,self.metadata,expected_certificate_sha256=self.certificate_hash,
                              work_root=self.root)

    def test_synthetic_round_trip_and_encrypted_failure_logs(self):
        receipt = self.export()
        cipher = self.output/receipt["ciphertext_file"]
        self.assertEqual(hashlib.sha256(cipher.read_bytes()).hexdigest(),receipt["ciphertext_sha256"])
        self.assertEqual({p.name for p in self.output.iterdir()},
                         {receipt["ciphertext_file"],"encrypted-receipt.json"})
        plain = self.root/"decrypted.tar.gz"
        subprocess.run(["openssl","cms","-decrypt","-binary","-inform","DER",
                        "-in",str(cipher),"-recip",str(self.cert),"-inkey",str(self.key),
                        "-out",str(plain)],check=True,capture_output=True,timeout=15)
        with tarfile.open(plain,"r:gz") as archive:
            self.assertEqual(set(archive.getnames()),
                             {"bundle-manifest.json","dist/Image","out/logs/failure.log","out/source/port-source.tar.gz"})
            manifest = json.load(archive.extractfile("bundle-manifest.json"))
            self.assertEqual(manifest["context"]["build_job_status"],"failure")
            for name, expected in manifest["files"].items():
                data = archive.extractfile(name).read()
                self.assertEqual(len(data),expected["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(),expected["sha256"])
        self.assertFalse(receipt["raw_images_or_log_bundles_publicly_uploaded"])
        self.assertFalse(receipt["phone_write_authorized"])
        self.assertNotIn("synthetic.key",(self.output/"encrypted-receipt.json").read_text())

    def test_a_tampered_authenticated_envelope_cannot_decrypt(self):
        receipt = self.export(); cipher = self.output/receipt["ciphertext_file"]
        damaged = bytearray(cipher.read_bytes()); damaged[-1] ^= 1; cipher.write_bytes(damaged)
        result = subprocess.run(["openssl","cms","-decrypt","-binary","-inform","DER",
                                 "-in",str(cipher),"-recip",str(self.cert),"-inkey",str(self.key),
                                 "-out",str(self.root/"bad.tar.gz")],capture_output=True,timeout=15)
        self.assertNotEqual(result.returncode,0)

    def test_wrong_or_missing_recipient_has_no_plaintext_fallback(self):
        for certificate in ("",self.certificate+"\n",self.key.read_text()):
            with self.subTest(bytes=len(certificate)),self.assertRaisesRegex(ValueError,"recipient"):
                self.export(certificate)
            self.assertFalse(self.output.exists())

    def test_nonregular_hidden_and_escaping_inputs_fail_closed(self):
        escaped = self.root/"dist/escaping-link"; escaped.symlink_to(self.key)
        with self.assertRaisesRegex(ValueError,"nonregular"):
            gather_files(self.root)
        escaped.unlink()
        hidden = self.root/"dist/.hidden"; hidden.write_bytes(b"fixture")
        with self.assertRaisesRegex(ValueError,"unsafe"):
            self.export()
        self.assertFalse(self.output.exists())

    def test_existing_unexpected_output_is_not_uploaded(self):
        self.output.mkdir(parents=True); (self.output/"unexpected-plaintext.img").write_bytes(b"fixture")
        with self.assertRaisesRegex(ValueError,"not empty"):
            self.export()

    def test_production_cli_refuses_non_ci_before_export(self):
        env = dict(os.environ); env.pop("GITHUB_ACTIONS",None)
        result = subprocess.run([sys.executable,"-m","scripts.encrypt_artifacts"],cwd=ROOT,env=env,
                                capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("CI-only",result.stderr)

    def test_branch_workflow_has_only_the_encrypted_artifact_upload(self):
        workflow = (ROOT/".github/workflows/mainline.yml").read_text()
        self.assertEqual(workflow.count("uses: actions/upload-artifact@"),1)
        self.assertIn("path: out/encrypted/",workflow)
        self.assertNotIn("path: dist/",workflow)
        self.assertNotIn("path: out/source/",workflow)
        self.assertIn("steps.encrypt.outcome == 'success'",workflow)
        self.assertIn("STAGE0_BUILD_RECIPIENT_CERT",workflow)
        self.assertIn("expected_commit:",workflow)
