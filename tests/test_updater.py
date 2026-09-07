from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backup_manager.updater import UpdateError, apply_staged, create_backup, restore_backup, run_migrations, validate_package

ROOT = Path(__file__).resolve().parents[1]


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.public = self.root / "public.pem"; self.public.write_bytes(public)

    def tearDown(self): self.temp.cleanup()

    def package(self, *, product="backup-manager-local", version="1.1.1", extra=None, signature=True, path_name="backup_manager/new.py"):
        content = b"value = 1\n"; digest = hashlib.sha256(content).hexdigest()
        aggregate = hashlib.sha256(path_name.encode() + b"\0" + digest.encode() + b"\n").hexdigest()
        manifest = {"product": product, "version": version, "minimum_version": "1.0.0-rc1", "maximum_version": "",
            "channel": "stable", "build_id": "test", "created_at": "2026-01-01T00:00:00Z", "payload_sha256": aggregate,
            "required_disk_bytes": 1, "requires_restart": False, "services_to_restart": [], "migrations": [],
            "files": [{"path": path_name, "sha256": digest, "size": len(content), "action": "add"}],
            "pre_checks": [], "post_checks": ["integrity-check"], "release_notes": "Teste"}
        canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        package = self.root / f"{version}.bmu"
        checksums = f"{digest}  payload/{path_name}\n".encode()
        with tarfile.open(package, "w:gz") as archive:
            items = {"manifest.json": canonical, "checksums.txt": checksums,
                     "signature": self.private.sign(canonical + b"\n" + checksums) if signature else b"invalid", f"payload/{path_name}": content}
            if extra: items.update(extra)
            for name, data in items.items():
                info = tarfile.TarInfo(name); info.size = len(data); archive.addfile(info, io.BytesIO(data))
        return package

    def assertCode(self, code, **kwargs):
        with self.assertRaises(UpdateError) as raised: validate_package(self.package(**kwargs), self.public)
        self.assertEqual(code, raised.exception.code)

    def test_valid_signed_package_and_plan(self):
        result = validate_package(self.package(), self.public)
        self.assertEqual("1.1.1", result.plan["to_version"])
        self.assertEqual(["backup_manager/new.py"], result.plan["added"])

    def test_official_builder_produces_package_accepted_by_validator(self):
        private_path = self.root / "private.pem"
        private_path.write_bytes(self.private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        subprocess.run([
            sys.executable, str(ROOT / "scripts/build-update-package.py"),
            "--version", "1.1.1", "--minimum-version", "1.0.0",
            "--private-key", str(private_path), "--output", str(self.root),
        ], cwd=ROOT, check=True, capture_output=True, text=True)
        result = validate_package(self.root / "backup-manager-local-1.1.1.bmu", self.public)
        self.assertEqual("1.1.1", result.plan["to_version"])

    def test_invalid_signature_product_and_downgrade(self):
        self.assertCode("SIGNATURE_INVALID", signature=False)
        self.assertCode("PRODUCT_MISMATCH", product="other")
        self.assertCode("DOWNGRADE_BLOCKED", version="0.9.9")

    def test_extra_file_and_forbidden_destination(self):
        self.assertCode("UNDECLARED_FILE", extra={"payload/extra.py": b"x"})
        self.assertCode("FILE_FORBIDDEN", path_name="data/database.sqlite3")

    def test_path_traversal_and_symlink(self):
        self.assertCode("PATH_TRAVERSAL", extra={"../escape": b"x"})
        package = self.package()
        malicious = self.root / "link.bmu"
        with tarfile.open(malicious, "w:gz") as out, tarfile.open(package, "r:gz") as source:
            for item in source.getmembers():
                data = source.extractfile(item).read() if item.isfile() else b""
                out.addfile(item, io.BytesIO(data) if item.isfile() else None)
            link = tarfile.TarInfo("payload/link"); link.type = tarfile.SYMTYPE; link.linkname = "/etc/passwd"; out.addfile(link)
        with self.assertRaises(UpdateError) as raised: validate_package(malicious, self.public)
        self.assertEqual("UNSAFE_MEMBER", raised.exception.code)

    def test_backup_apply_migration_and_restore(self):
        app = self.root / "app"; (app / "backup_manager").mkdir(parents=True)
        managed = app / "backup_manager/a.py"; managed.write_text("old")
        db = self.root / "db.sqlite3"
        conn = sqlite3.connect(db); conn.execute("CREATE TABLE value(n INTEGER)"); conn.execute("INSERT INTO value VALUES(1)"); conn.commit(); conn.close()
        backup = create_backup(db, app, self.root / "backups", "op", ["backup_manager/a.py"])
        staging = self.root / "stage"; (staging / "payload/backup_manager").mkdir(parents=True); (staging / "migrations").mkdir()
        (staging / "payload/backup_manager/a.py").write_text("new")
        (staging / "migrations/999_test.sql").write_text("CREATE TABLE added(id INTEGER);")
        manifest = {"files": [{"path": "backup_manager/a.py", "action": "replace"}], "migrations": ["999_test.sql"]}
        apply_staged(staging, app, manifest); run_migrations(db, staging, manifest["migrations"])
        self.assertEqual("new", managed.read_text())
        restore_backup(db, app, backup)
        self.assertEqual("old", managed.read_text())
        conn = sqlite3.connect(db); self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE name='added'").fetchone()); conn.close()


if __name__ == "__main__": unittest.main()
