from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
LETSENCRYPT_TEMPLATE = (
    ROOT / "deploy" / "nginx" / "backup-manager-local-letsencrypt.conf.template"
).read_text(encoding="utf-8")
HTTP_TEMPLATE = (
    ROOT / "deploy" / "nginx" / "backup-manager-local-http.conf"
).read_text(encoding="utf-8")


class NginxInstallerRegressionTest(unittest.TestCase):
    def test_letsencrypt_template_never_references_self_signed_certificate(self) -> None:
        rendered = LETSENCRYPT_TEMPLATE.replace("__DOMAIN__", "backup.example.com")
        self.assertNotIn("/etc/ssl/backup-manager-local", rendered)
        self.assertIn(
            "/etc/letsencrypt/live/backup.example.com/fullchain.pem", rendered
        )
        self.assertIn(
            "/etc/letsencrypt/live/backup.example.com/privkey.pem", rendered
        )

    def test_letsencrypt_https_block_is_not_a_default_server(self) -> None:
        https_blocks = [
            block
            for block in re.findall(r"server\s*\{(.*?)\n\}", LETSENCRYPT_TEMPLATE, re.S)
            if re.search(r"listen\s+(?:\[::\]:)?443\s+ssl", block)
        ]
        self.assertEqual(len(https_blocks), 1)
        self.assertNotIn("default_server", https_blocks[0])

    def test_installer_uses_http_template_without_self_signed_generation(self) -> None:
        self.assertIn("NGINX_HTTP_TEMPLATE", INSTALLER)
        self.assertNotIn("openssl req -x509", INSTALLER)
        self.assertNotIn("/etc/ssl/backup-manager-local", INSTALLER)

    def test_deploy_update_preserves_letsencrypt_configuration(self) -> None:
        deploy_update = (ROOT / "scripts" / "deploy-update.sh").read_text(encoding="utf-8")
        self.assertIn("Nginx e certificados preservados", deploy_update)
        self.assertNotIn("install -m 0644 \"${NGINX_SOURCE}\"", deploy_update)

    def test_proxy_does_not_duplicate_application_security_headers(self) -> None:
        application_headers = (
            "Content-Security-Policy",
            "Referrer-Policy",
            "Strict-Transport-Security",
            "X-Content-Type-Options",
            "X-Frame-Options",
        )
        for template in (HTTP_TEMPLATE, LETSENCRYPT_TEMPLATE):
            for header in application_headers:
                self.assertNotIn(f"add_header {header}", template)
            self.assertIn("add_header Permissions-Policy", template)


if __name__ == "__main__":
    unittest.main()
