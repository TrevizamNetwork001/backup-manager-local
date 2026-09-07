from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode, urlsplit
from wsgiref.util import setup_testing_defaults

os.environ.setdefault("BACKUP_MANAGER_DATA", tempfile.mkdtemp(prefix="backup-manager-settings-test-"))
os.environ.setdefault("BACKUP_MANAGER_DB", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "test.sqlite3"))
os.environ.setdefault("BACKUP_MANAGER_STORAGE_ROOT", tempfile.mkdtemp(prefix="backup-manager-settings-storage-"))

from backup_manager.app import application, get_setting  # noqa: E402
from backup_manager.db import DATA_DIR, TEMP_ADMIN_PASSWORD, connect, migrate  # noqa: E402
from backup_manager.settings_center import (  # noqa: E402
    ensure_roles,
    export_configuration,
    import_configuration,
    parse_configuration,
    role_description,
    run_https_helper,
    validate_domain,
    validate_general,
)
from backup_manager.security import hash_password  # noqa: E402


class Client:
    def __init__(self) -> None:
        self.token = ""

    @property
    def cookie(self) -> str:
        return f"bm_session={self.token}" if self.token else ""

    def request(self, method: str, path: str, data: dict[str, str] | None = None):
        from http.cookies import SimpleCookie
        body = urlencode(data or {}).encode()
        target = urlsplit(path)
        environ: dict = {}
        setup_testing_defaults(environ)
        environ.update({"REQUEST_METHOD": method, "PATH_INFO": target.path, "QUERY_STRING": target.query,
                        "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/x-www-form-urlencoded",
                        "wsgi.input": io.BytesIO(body)})
        if self.cookie:
            environ["HTTP_COOKIE"] = self.cookie
        captured: dict = {}

        def start_response(status, headers):
            captured["status"], captured["headers"] = status, dict(headers)

        response = b"".join(application(environ, start_response))
        cookie = SimpleCookie(captured["headers"].get("Set-Cookie", ""))
        if cookie.get("bm_session") and cookie["bm_session"].value:
            self.token = cookie["bm_session"].value
        return captured["status"], captured["headers"], response

    def multipart(self, path: str, fields: dict[str, str], filename: str, payload: bytes):
        boundary = "----SettingsBoundary"
        parts: list[bytes] = []
        for key, value in fields.items():
            parts += [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n".encode(),
                      value.encode(), b"\r\n"]
        parts += [f"--{boundary}\r\nContent-Disposition: form-data; name=\"configuration\"; filename=\"{filename}\"\r\n".encode(),
                  b"Content-Type: application/json\r\n\r\n", payload, b"\r\n", f"--{boundary}--\r\n".encode()]
        body = b"".join(parts)
        environ: dict = {}
        setup_testing_defaults(environ)
        environ.update({"REQUEST_METHOD": "POST", "PATH_INFO": path, "QUERY_STRING": "", "CONTENT_LENGTH": str(len(body)),
                        "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}", "wsgi.input": io.BytesIO(body)})
        if self.cookie:
            environ["HTTP_COOKIE"] = self.cookie
        captured: dict = {}
        response = b"".join(application(environ, lambda status, headers: captured.update(status=status, headers=dict(headers))))
        return captured["status"], captured["headers"], response


class FinalSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        migrate(31)
        self.client = Client()

    def login_admin(self) -> None:
        self.client.request("POST", "/login", {"username": "admin", "password": TEMP_ADMIN_PASSWORD})
        self.client.request("POST", "/change-password", {"current_password": TEMP_ADMIN_PASSWORD,
                            "new_password": "NovaSenha!2026", "confirm_password": "NovaSenha!2026"})

    def csrf(self) -> str:
        return hashlib.sha256(("session-csrf:" + self.client.token).encode()).hexdigest()

    def test_general_settings_are_validated_and_language_stays_portuguese(self) -> None:
        values = validate_general({"installation_name": "Backup Matriz", "provider_name": "Empresa",
            "timezone": "America/Sao_Paulo", "date_format": "dmy", "time_format": "24h",
            "home_page": "/reports", "language": "en_US"})
        self.assertEqual(values["language"], "pt_BR")
        self.assertEqual(values["home_page"], "/reports")
        with self.assertRaises(ValueError):
            validate_general({"installation_name": "", "provider_name": "Empresa", "timezone": "UTC"})

    def test_profiles_are_simple_and_described(self) -> None:
        self.client.request("GET", "/login")
        with connect() as conn:
            ensure_roles(conn)
            roles = {row["slug"]: (row["name"], row["can_download"], row["can_admin"])
                     for row in conn.execute("SELECT * FROM roles")}
        self.assertEqual(roles["admin"], ("Administrador", 1, 1))
        self.assertEqual(roles["operator"], ("Operador", 1, 0))
        self.assertEqual(roles["read_download"], ("Leitura", 1, 0))
        self.assertIn("Executa backups", role_description("operator"))

    def test_user_can_change_avatar_icon_from_account_menu(self) -> None:
        self.login_admin()
        status, headers, _ = self.client.request("POST", "/profile/avatar", {
            "csrf_token": self.csrf(), "avatar_icon": "shield",
        })
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/")
        with connect() as conn:
            self.assertEqual(get_setting(conn, "profile_avatar_icon_1"), "shield")
        status, _, body = self.client.request("GET", "/settings")
        self.assertTrue(status.startswith("200"))
        self.assertIn(b"Trocar \xc3\xadcone do avatar", body)
        self.assertIn(b'class="topbar-account-menu"', body)

    def test_configuration_export_has_inventory_but_no_secrets(self) -> None:
        self.client.request("GET", "/login")
        with connect() as conn:
            conn.execute("INSERT INTO equipment(hostname,name,ip_address) VALUES('router-config','Router','10.0.0.1')")
            payload = export_configuration(conn)
        data = parse_configuration(payload)
        self.assertEqual(data["format"], "backup-manager-configuration")
        self.assertEqual(data["equipment"][0]["hostname"], "router-config")
        serialized = payload.decode().lower()
        for forbidden in ("password_hash", "token_encrypted", "private_key", "client_secret", "refresh_token"):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse(data["secrets_included"])

    def test_configuration_import_restores_inventory_without_credentials(self) -> None:
        self.client.request("GET", "/login")
        with connect() as conn:
            conn.execute("INSERT INTO equipment(hostname,name,ip_address,ssh_backup_driver) VALUES('router-move','Router Move','10.0.0.2','generic_ssh')")
            payload = export_configuration(conn)
            conn.execute("DELETE FROM equipment WHERE hostname='router-move'")
            counts = import_configuration(conn, payload)
            restored = conn.execute("SELECT hostname,ssh_backup_driver FROM equipment WHERE hostname='router-move'").fetchone()
            credentials = conn.execute("SELECT COUNT(*) FROM device_credentials").fetchone()[0]
        self.assertEqual(counts["equipment"], 1)
        self.assertEqual(tuple(restored), ("router-move", "generic_ssh"))
        self.assertEqual(credentials, 0)

    def test_https_requires_confirmation_and_uses_only_letsencrypt_helper(self) -> None:
        self.assertEqual(validate_domain("Backup.Exemplo.com.br"), "backup.exemplo.com.br")
        with self.assertRaises(ValueError):
            run_https_helper("issue", "backup.exemplo.com.br", "admin@example.com", confirmed=False)
        completed = subprocess.CompletedProcess([], 0, "HTTPS instalado", "")
        runner = mock.Mock(return_value=completed)
        ok, detail = run_https_helper("issue", "backup.exemplo.com.br", "admin@example.com",
                                     confirmed=True, runner=runner)
        self.assertTrue(ok)
        self.assertIn("HTTPS", detail)
        command = runner.call_args.args[0]
        self.assertIn("--confirm", command)
        self.assertNotIn("self-signed", " ".join(command).lower())

    def test_settings_center_export_import_permissions_and_empty_state(self) -> None:
        self.login_admin()
        status, _, body = self.client.request("GET", "/settings?tab=users")
        self.assertTrue(status.startswith("200"))
        for label in ("Geral", "Usuários", "Acesso e HTTPS", "Notificações"):
            self.assertIn(label.encode(), body)
        status, headers, payload = self.client.request("GET", "/settings/configuration/export")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        status, headers, _ = self.client.multipart("/settings/configuration/import", {
            "csrf_token": self.csrf(), "confirmation": "IMPORTAR CONFIGURACAO"}, "configuration.json", payload)
        self.assertTrue(status.startswith("302"))
        self.assertIn("tab=configuration", headers["Location"])

    def test_operator_cannot_administer_or_export_configuration(self) -> None:
        self.client.request("GET", "/login")
        with connect() as conn:
            ensure_roles(conn)
            role_id = conn.execute("SELECT id FROM roles WHERE slug='operator'").fetchone()[0]
            conn.execute("INSERT INTO users(username,full_name,password_hash,role_id,must_change_password) VALUES(?,?,?,?,0)",
                         ("operador", "Operador", hash_password("SenhaOperador!2026"), role_id))
        operator = Client()
        operator.request("POST", "/login", {"username": "operador", "password": "SenhaOperador!2026"})
        status, _, _ = operator.request("GET", "/settings")
        self.assertTrue(status.startswith("403"))
        status, _, _ = operator.request("GET", "/settings/configuration/export")
        self.assertTrue(status.startswith("403"))

    def test_responsive_settings_styles_keep_existing_breakpoints(self) -> None:
        css = (Path(__file__).parents[1] / "static" / "settings.css").read_text()
        self.assertIn(".settings-tabs", css)
        self.assertIn(".form-grid", css)
        for breakpoint in ("860px", "520px"):
            self.assertIn(f"max-width: {breakpoint}", css)


if __name__ == "__main__":
    unittest.main()
