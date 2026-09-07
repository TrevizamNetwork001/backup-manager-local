from __future__ import annotations

import base64
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backup_manager.update_repository import RepositoryError, SemVer, canonical, select_release, validate_index, validate_url
from backup_manager.cli import update_check_command


class UpdateRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.private=Ed25519PrivateKey.generate()
        self.public=self.root/"public.pem"
        self.public.write_bytes(self.private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))

    def tearDown(self): self.temp.cleanup()

    def release(self, version="1.0.1", channel="stable", **changes):
        value={"version":version,"channel":channel,"build_id":"build-1","published_at":"2026-01-01T00:00:00Z",
               "minimum_version":"1.0.0","maximum_version":"","package_url":"https://updates.example/releases/x.bmu",
               "package_sha256":"a"*64,"package_size":100,"signature_url":"https://updates.example/releases/x.bmu.sig",
               "release_notes":"Notas","mandatory":False,"deprecated":False,"security_update":False}
        value.update(changes); return value

    def index(self, releases=None, product="backup-manager-local"):
        stable=[self.release()] if releases is None else releases
        return {"product":product,"generated_at":"2026-01-01T00:00:00Z","channels":{"stable":{"latest":stable[-1]["version"] if stable else "","releases":stable},"rc":{"latest":"","releases":[]}}}

    def signed(self, value):
        raw=canonical(value); return raw,self.private.sign(raw)

    def assertCode(self, code, callback):
        with self.assertRaises(RepositoryError) as raised: callback()
        self.assertEqual(code,raised.exception.code)

    def test_valid_index_and_base64_signature(self):
        raw,sig=self.signed(self.index())
        self.assertEqual("backup-manager-local",validate_index(raw,sig,self.public)["product"])
        self.assertEqual("backup-manager-local",validate_index(raw,base64.b64encode(sig),self.public)["product"])

    def test_invalid_signature_schema_and_product(self):
        raw,_=self.signed(self.index()); self.assertCode("SIGNATURE_INVALID",lambda: validate_index(raw,b"x"*64,self.public))
        value=self.index(); value["extra"]=1; raw,sig=self.signed(value); self.assertCode("INDEX_SCHEMA",lambda: validate_index(raw,sig,self.public))
        raw,sig=self.signed(self.index(product="other")); self.assertCode("PRODUCT_MISMATCH",lambda: validate_index(raw,sig,self.public))

    def test_versions_upgrade_downgrade_equal_and_incompatible(self):
        index=self.index([self.release("0.9.9"),self.release("1.0.0"),self.release("1.0.1",minimum_version="2.0.0"),self.release("1.1.0")])
        self.assertEqual("1.1.0",select_release(index,"stable","1.0.0")["version"])
        self.assertIsNone(select_release(self.index([self.release("0.9.9"),self.release("1.0.0")]),"stable","1.0.0"))

    def test_stable_excludes_rc_and_rc_includes_new_stable(self):
        index=self.index([self.release("1.1.0")]); index["channels"]["rc"]={"latest":"1.2.0-rc1","releases":[self.release("1.2.0-rc1","rc")]}
        self.assertEqual("1.1.0",select_release(index,"stable","1.0.0")["version"])
        self.assertEqual("1.2.0-rc1",select_release(index,"rc","1.0.0")["version"])
        self.assertLess(SemVer.parse("1.1.0-rc1"),SemVer.parse("1.1.0"))

    def test_deprecated_is_ignored(self):
        self.assertIsNone(select_release(self.index([self.release(deprecated=True)]),"stable","1.0.0"))

    def test_http_localhost_literal_and_private_dns_are_blocked(self):
        public=lambda *a,**k:[(2,1,6,"",("93.184.216.34",443))]
        private=lambda *a,**k:[(2,1,6,"",("10.0.0.2",443))]
        self.assertCode("HTTPS_REQUIRED",lambda: validate_url("http://updates.example/x",resolver=public))
        self.assertCode("HOST_BLOCKED",lambda: validate_url("https://localhost/x",resolver=public))
        self.assertCode("IP_LITERAL_BLOCKED",lambda: validate_url("https://8.8.8.8/x",resolver=public))
        self.assertCode("PRIVATE_ADDRESS_BLOCKED",lambda: validate_url("https://updates.example/x",resolver=private))
        self.assertEqual("https://updates.example/x",validate_url("https://updates.example/x",resolver=public))

    def test_credentials_and_allowlist_are_blocked(self):
        public=lambda *a,**k:[(2,1,6,"",("93.184.216.34",443))]
        self.assertCode("URL_INVALID",lambda: validate_url("https://user:pass@updates.example/x",resolver=public))
        self.assertCode("HOST_NOT_ALLOWED",lambda: validate_url("https://updates.example/x",allowed_hosts={"other.example"},resolver=public))

    def test_deploy_installs_repository_units_for_current_service_account(self):
        root=Path(__file__).resolve().parents[1]
        deploy=(root/"scripts/deploy-update.sh").read_text(encoding="utf-8")
        for name in ("backup-manager-update-check", "backup-manager-update-download"):
            self.assertIn(name,deploy)
        self.assertIn('systemctl enable --now "${UPDATE_CHECK_NAME}.timer"',deploy)
        for name in ("backup-manager-update-check.service", "backup-manager-update-download.service"):
            unit=(root/"deploy/systemd"/name).read_text(encoding="utf-8")
            self.assertIn("User=root",unit)
            self.assertIn("Group=root",unit)

    def test_disabled_repository_is_a_successful_scheduled_skip(self):
        context=mock.MagicMock()
        with mock.patch("backup_manager.cli.connect",return_value=context), \
             mock.patch("backup_manager.cli.check_updates",side_effect=RepositoryError("REPOSITORY_DISABLED")), \
             mock.patch("sys.stdout",new_callable=io.StringIO) as output:
            self.assertEqual(0,update_check_command(str(self.public)))
        self.assertEqual("REPOSITORY_DISABLED",output.getvalue().strip())


if __name__=="__main__": unittest.main()
