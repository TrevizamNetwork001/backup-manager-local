from __future__ import annotations

import hashlib
import importlib.util
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_first_release", ROOT / "docs/release-tools/prepare_1_1_0.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleasePreparationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.keys = self.root / "keys"
        self.keys.mkdir()
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key()
        self.write_private(self.private)
        (self.keys / "update-public-key.pem").write_bytes(self.public.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        der = self.public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        patcher = mock.patch.object(release, "PUBLIC_SHA256", hashlib.sha256(der).hexdigest())
        patcher.start()
        self.addCleanup(patcher.stop)
        # Exercise signing against the current test source, independently of
        # the immutable production fingerprint for the original 1.1.0 release.
        builder = runpy.run_path(str(ROOT / "scripts/build-update-package.py"))
        aggregate = hashlib.sha256()
        for relative in sorted(builder["collect"](), key=lambda path: path.as_posix()):
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            aggregate.update(relative.as_posix().encode() + b"\0" + digest.encode() + b"\n")
        payload_patch = mock.patch.object(release, "PAYLOAD_SHA256", aggregate.hexdigest())
        payload_patch.start()
        self.addCleanup(payload_patch.stop)
        self.output = self.root / "ready"

    def write_private(self, private):
        (self.keys / "update-private-key.pem").write_bytes(private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    def prepare(self):
        return release.prepare(ROOT, self.keys, self.output, "Example/backup-manager-updates")

    def test_signed_package_and_github_index_without_private_material(self):
        report = self.prepare()
        self.assertTrue(report["signed_and_validated"])
        self.assertFalse(report["uploaded"])
        pages = self.output / "pages"
        raw = (pages / "updates.json").read_bytes()
        self.public.verify((pages / "updates.json.sig").read_bytes(), raw)
        item = json.loads(raw)["channels"]["stable"]["releases"][0]
        self.assertEqual(item["package_url"], "https://github.com/Example/backup-manager-updates/releases/download/v1.1.0/backup-manager-local-1.1.0.bmu")
        self.assertEqual((pages / ".gitattributes").read_text(), "* -text\n")
        self.assertEqual(list(self.output.rglob("*.pem")), [])
        self.assertTrue((self.output / "releases/backup-manager-local-1.1.0.bmu").is_file())
        with self.assertRaisesRegex(ValueError, "ja existe"):
            self.prepare()

    def test_mismatched_private_key_is_rejected_before_output(self):
        self.write_private(Ed25519PrivateKey.generate())
        with self.assertRaises(InvalidSignature):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_untrusted_public_key_is_rejected(self):
        with mock.patch.object(release, "PUBLIC_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "nao corresponde"):
                self.prepare()
        self.assertFalse(self.output.exists())

    def test_changed_source_is_rejected_before_output(self):
        with mock.patch.object(release, "PAYLOAD_SHA256", "0" * 64):
            with self.assertRaisesRegex(ValueError, "codigo difere"):
                self.prepare()
        self.assertFalse(self.output.exists())

    def test_keys_and_output_cannot_be_inside_source(self):
        with self.assertRaisesRegex(ValueError, "fora da pasta"):
            release.prepare(ROOT, ROOT / "keys", self.output, "Example/updates")
        with self.assertRaisesRegex(ValueError, "fora da pasta"):
            release.prepare(ROOT, self.keys, ROOT / "release-output", "Example/updates")


if __name__ == "__main__":
    unittest.main()
