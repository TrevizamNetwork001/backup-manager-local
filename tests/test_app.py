from __future__ import annotations

import os
import re
import json
import hashlib
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit
from wsgiref.util import setup_testing_defaults

os.environ["BACKUP_MANAGER_DATA"] = tempfile.mkdtemp(prefix="backup-manager-test-")
os.environ["BACKUP_MANAGER_DB"] = os.path.join(os.environ["BACKUP_MANAGER_DATA"], "test.sqlite3")
os.environ["BACKUP_MANAGER_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="backup-manager-storage-")
os.environ["BACKUP_MANAGER_SECRET_KEY"] = os.path.join(os.environ["BACKUP_MANAGER_DATA"], "secret.key")
os.environ["BACKUP_MANAGER_SERVICE_SNAPSHOT"] = os.path.join(os.environ["BACKUP_MANAGER_DATA"], "service-status.json")

from backup_manager.app import DISPLAY_TIMEZONE, application, client_ip, local_dt, notification_next_send, quantity, relative_time, short_identifier  # noqa: E402
from backup_manager.db import TEMP_ADMIN_PASSWORD, connect, integrity_check, migrate  # noqa: E402
from backup_manager.jobs import run_once, stats  # noqa: E402
from backup_manager.security import decrypt_secret, encrypt_secret, hash_password  # noqa: E402
from backup_manager.olt_operations import complete_received_file as complete_olt_received_file  # noqa: E402
from backup_manager.ssh import SSHBackupError, create_credential, install_routeros_script  # noqa: E402
from backup_manager.mikrotik_ftp_scripts import ScriptConfig  # noqa: E402
from backup_manager.ftp import FTPAccountError, create_account, validate_username  # noqa: E402
from backup_manager.ftp_importer import scan_once  # noqa: E402
from backup_manager.lifecycle import (CONFIRMATION, EMPTY_TRASH_CONFIRMATION, PURGE_CONFIRMATION,
                                      effective_policy, execute, health_check, restore, save_policy, simulate)  # noqa: E402
from backup_manager.observability import backup_status, dashboard_snapshot, security_activity  # noqa: E402
from backup_manager.notifications import dispatch as notification_dispatch, record_condition  # noqa: E402
from backup_manager.cli import jobs_check, notification_check  # noqa: E402


class WsgiClient:
    def __init__(self) -> None:
        self.cookie = ""

    def request(self, method: str, path: str, data: dict[str, str] | None = None,
                headers: dict[str, str] | None = None):
        body = urlencode(data or {}).encode("utf-8")
        parsed = urlsplit(path)
        environ = {}
        setup_testing_defaults(environ)
        input_file = tempfile.SpooledTemporaryFile()
        try:
            environ.update(
                {
                    "REQUEST_METHOD": method,
                    "PATH_INFO": parsed.path,
                    "QUERY_STRING": parsed.query,
                    "CONTENT_LENGTH": str(len(body)),
                    "CONTENT_TYPE": "application/x-www-form-urlencoded",
                    "REMOTE_ADDR": "127.0.0.1",
                    "wsgi.input": input_file,
                }
            )
            environ["wsgi.input"].write(body)
            environ["wsgi.input"].seek(0)
            if self.cookie:
                environ["HTTP_COOKIE"] = self.cookie
            for key, value in (headers or {}).items():
                environ["HTTP_" + key.upper().replace("-", "_")] = value
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            response_body = b"".join(application(environ, start_response))
            for key, value in captured["headers"]:
                if key.lower() == "set-cookie":
                    cookie = SimpleCookie(value)
                    morsel = cookie.get("bm_session")
                    if morsel and morsel.value:
                        self.cookie = f"bm_session={morsel.value}"
            return captured["status"], dict(captured["headers"]), response_body.decode("utf-8")
        finally:
            input_file.close()

    def multipart(self, path: str, fields: dict[str, str], file_name: str, content: bytes):
        boundary = "----BackupManagerTestBoundary"
        parts: list[bytes] = []
        for key, value in fields.items():
            parts.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("ascii"),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        parts.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="backup_file"; filename="{file_name}"\r\n'.encode("utf-8"),
                b"Content-Type: application/octet-stream\r\n\r\n",
                content,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        body = b"".join(parts)
        environ = {}
        setup_testing_defaults(environ)
        input_file = tempfile.SpooledTemporaryFile()
        try:
            environ.update(
                {
                    "REQUEST_METHOD": "POST",
                    "PATH_INFO": path,
                    "CONTENT_LENGTH": str(len(body)),
                    "CONTENT_TYPE": f"multipart/form-data; boundary={boundary}",
                    "wsgi.input": input_file,
                }
            )
            environ["wsgi.input"].write(body)
            environ["wsgi.input"].seek(0)
            if self.cookie:
                environ["HTTP_COOKIE"] = self.cookie
            captured = {}

            def start_response(status, headers):
                captured["status"] = status
                captured["headers"] = headers

            response_body = b"".join(application(environ, start_response))
            return captured["status"], dict(captured["headers"]), response_body
        finally:
            input_file.close()


class BackupManagerAppTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(os.environ["BACKUP_MANAGER_DATA"], ignore_errors=True)
        shutil.rmtree(os.environ["BACKUP_MANAGER_STORAGE_ROOT"], ignore_errors=True)
        os.makedirs(os.environ["BACKUP_MANAGER_DATA"], exist_ok=True)
        os.makedirs(os.environ["BACKUP_MANAGER_STORAGE_ROOT"], exist_ok=True)
        os.environ["BACKUP_MANAGER_SECRET_KEY"] = os.path.join(os.environ["BACKUP_MANAGER_DATA"], "secret.key")
        migrate(31)
        self.client = WsgiClient()

    def save_telegram(self, token: str, chat_id: str) -> None:
        status, _, _ = self.client.request("POST", "/settings/notifications", {
            "is_enabled": "1", "token": token, "chat_id": chat_id, "cooldown_minutes": "60",
            "timezone": "America/Sao_Paulo", "maintenance_start": "23:00", "maintenance_end": "01:00",
            "daily_time": "08:00", "weekly_day": "0", "weekly_time": "08:00",
            "csrf_token": self.settings_csrf(),
        })
        self.assertTrue(status.startswith("302"))

    def settings_csrf(self) -> str:
        session = self.client.cookie.split("=", 1)[1]
        return hashlib.sha256(("session-csrf:" + session).encode()).hexdigest()

    def create_equipment_row(self, hostname: str, driver: str = "cisco_ios") -> int:
        with connect() as conn:
            vendor_name = "MikroTik" if driver == "mikrotik_routeros" else "Cisco" if driver == "cisco_ios" else None
            vendor_id = conn.execute("SELECT id FROM vendors WHERE name=?", (vendor_name,)).fetchone()[0] if vendor_name else None
            return conn.execute("""INSERT INTO equipment(name,hostname,ip_address,vendor_id,is_active,ssh_backup_driver,ssh_port)
                VALUES(?,?,?,?,1,?,22)""", (hostname, hostname, "192.0.2.99", vendor_id, driver)).lastrowid

    def test_utc_timestamps_are_presented_in_configured_timezone(self) -> None:
        token = DISPLAY_TIMEZONE.set("America/Sao_Paulo")
        try:
            self.assertEqual(local_dt("2026-07-11 23:33:25"), "2026-07-11 20:33:25")
            self.assertEqual(local_dt("2026-07-11T23:33:25+00:00"), "2026-07-11 20:33:25")
        finally:
            DISPLAY_TIMEZONE.reset(token)

    def test_client_ip_trusts_only_local_reverse_proxy(self) -> None:
        self.assertEqual("198.51.100.25", client_ip({"REMOTE_ADDR": "127.0.0.1", "HTTP_X_REAL_IP": "198.51.100.25"}))
        self.assertEqual("203.0.113.8", client_ip({"REMOTE_ADDR": "203.0.113.8", "HTTP_X_REAL_IP": "198.51.100.25"}))
        self.assertEqual("127.0.0.1", client_ip({"REMOTE_ADDR": "127.0.0.1", "HTTP_X_REAL_IP": "forged"}))
        self.assertEqual("", client_ip({"REMOTE_ADDR": "invalid"}))
        self.login_ready_admin()
        status, _, page = self.client.request("GET", "/equipment", headers={"X-Real-IP": "198.51.100.25"})
        self.assertTrue(status.startswith("200"))
        self.assertIn("IP 198.51.100.25", page)
        self.client.request("POST", "/equipment", {
            "hostname": "ip-audit", "ip_address": "192.0.2.44",
            "ssh_backup_driver": "generic_ssh", "ssh_port": "22",
        }, headers={"X-Real-IP": "198.51.100.25"})
        with connect() as conn:
            row = conn.execute("SELECT ip_address FROM audit_log WHERE action='created' ORDER BY id DESC").fetchone()
            self.assertEqual("198.51.100.25", row["ip_address"])
        status, _, audit_page = self.client.request("GET", "/audit", headers={"X-Real-IP": "198.51.100.25"})
        self.assertTrue(status.startswith("200"))
        self.assertIn("IP de origem", audit_page)
        self.assertIn("198.51.100.25", audit_page)

    def test_security_headers_secure_cookie_and_csrf_enforcement(self) -> None:
        self.login_ready_admin()
        status, headers, _ = self.client.request(
            "POST", "/login", {"username": "admin", "password": "NovaSenha!2026"},
            {"X-Forwarded-Proto": "https", "X-Real-IP": "198.51.100.25"},
        )
        self.assertTrue(status.startswith("302"))
        self.assertIn("Secure", headers["Set-Cookie"])

        status, headers, page = self.client.request("GET", "/", headers={"X-Forwarded-Proto": "https"})
        self.assertTrue(status.startswith("200"))
        self.assertEqual("DENY", headers["X-Frame-Options"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertIn("max-age=31536000", headers["Strict-Transport-Security"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn("form-action 'self' https://accounts.google.com", headers["Content-Security-Policy"])
        self.assertIn('meta name="csrf-token"', page)
        self.assertIn('name="csrf_token"', page)

        status, _, _ = self.client.request(
            "POST", "/logout", headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertTrue(status.startswith("403"))
        status, _, _ = self.client.request("GET", "/")
        self.assertTrue(status.startswith("200"))

    def test_login_is_temporarily_throttled_after_repeated_failures(self) -> None:
        for _ in range(8):
            status, _, _ = self.client.request("POST", "/login", {"username": "admin", "password": "incorreta"})
            self.assertTrue(status.startswith("200"))
        status, headers, body = self.client.request("POST", "/login", {"username": "admin", "password": "incorreta"})
        self.assertTrue(status.startswith("429"))
        self.assertEqual("900", headers["Retry-After"])
        self.assertIn("Muitas tentativas", body)

    def test_distributed_login_attempts_appear_in_dashboard_and_audit_report(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            for index in range(25):
                conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                    VALUES(NULL,'login_failed','user','admin','Credenciais inválidas',?)""", (f"198.51.100.{index + 1}",))
            security = security_activity(conn)
        self.assertEqual("red", security["level"])
        self.assertEqual(25, security["failed_10m"])
        self.assertEqual(25, security["ips_10m"])
        self.assertEqual("admin", security["top_accounts"][0]["value"])

        status, _, dashboard = self.client.request("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Segurança de acesso", dashboard)
        self.assertIn("25</strong><span>IPs distintos", dashboard)
        status, _, audit_page = self.client.request("GET", "/audit")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Central de segurança", audit_page)
        self.assertIn("Contas mais visadas", audit_page)
        self.assertIn("198.51.100.1", audit_page)

    def test_next_notification_summary_uses_timezone_and_disabled_state(self) -> None:
        now = datetime(2026, 7, 15, 23, 30, tzinfo=timezone.utc)
        self.assertEqual("16/07/2026 às 08:00", notification_next_send(True, "08:00", "America/Sao_Paulo", now=now))
        self.assertEqual("Desativado", notification_next_send(False, "08:00", "America/Sao_Paulo", now=now))

    def test_equipment_form_lists_generic_vendor_connection_methods(self) -> None:
        self.login_ready_admin()
        status, _, page = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200"))
        self.assertIn("data-connection-method", page)
        self.assertIn("data-access-port", page)
        for group in ("OLTs", "Roteadores", "Switches", "Genérico"):
            self.assertIn(f'<optgroup label="{group}">', page)
        for value, label in (
            ("zte_olt_ssh_ftp", "ZTE — OLT (SSH + FTP)"),
            ("intelbras_gpon_ssh_ftp", "Intelbras — OLT GPON 8820i/G08/G16 (SSH + FTP interativo)"),
            ("intelbras_epon_ssh_ftp", "Intelbras — OLT EPON 4840 E (SSH + FTP)"),
            ("vsol_olt_ssh_ftp", "VSOL — OLT (SSH + FTP)"),
            ("vsol_olt_telnet_cli", "VSOL V1600GT — OLT (backup direto via Telnet)"),
            ("parks_olt_100_200_ssh_ftp", "Parks — OLT 100xx/200xx (SSH + FTP)"),
            ("parks_olt_300_400_ssh_ftp", "Parks — OLT 300xx/400xx (SSH + FTP)"),
            ("cdata_olt_ssh_ftp", "C-DATA — OLT GPON (SSH + FTP)"),
            ("fiberhome_olt_telnet_ftp", "FiberHome — OLT (Telnet + FTP)"),
            ("datacom_dmos_ssh", "Datacom — OLT DM461X (DmOS via SSH)"),
            ("huawei_olt_ssh_ftp", "Huawei — OLT (SSH + FTP)"),
        ):
            self.assertIn(f'<option value="{value}"', page)
            protocol = ("Telnet/FTP" if value == "fiberhome_olt_telnet_ftp" else
                        "Telnet" if value == "vsol_olt_telnet_cli" else "SSH")
            self.assertIn(f'>{protocol} — {label}</option>', page)

    def test_legacy_vendor_method_is_visible_but_requires_model_review(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='parks_ssh' WHERE id=1")
            conn.commit()
        status, _, page = self.client.request("GET", "/equipment/1/edit")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Método legado — revisão necessária", page)
        self.assertIn('value="parks_ssh" selected', page)
        self.assertIn("Legado Parks — selecione a família", page)

    def test_inactive_equipment_status_is_editable_and_ssh_test_explains_block(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET is_active=0 WHERE id=1")
            conn.commit()
        status, _, page = self.client.request("GET", "/equipment/1/edit")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Status do equipamento", page)
        self.assertIn('<option value="0" selected>Inativo</option>', page)
        status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
        self.assertTrue(status.startswith("302"))
        status, _, detail = self.client.request("GET", headers["Location"])
        self.assertTrue(status.startswith("200"))
        self.assertIn("O equipamento está inativo", detail)
        self.assertIn("Ative-o em Editar equipamento", detail)

    def test_equipment_name_opens_detail_page(self) -> None:
        self.login_ready_admin()
        status, _, page = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200"))
        self.assertIn(
            'class="equipment-name" href="/equipment/1"',
            page,
        )

    def test_ftp_push_olt_does_not_offer_generic_ssh_backup(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='huawei_olt_ssh_ftp' WHERE id=1")
            conn.commit()
        status, _, page = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Conta FTP", page)
        self.assertIn("Configurar FTP", page)
        self.assertNotIn("Executar backup SSH agora", page)
        self.assertIn('<option value="ftp" selected', page)

    def test_ftp_push_olt_accepts_scheduled_job(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='vsol_olt_ssh_ftp' WHERE id=1")
        status, headers, _ = self.client.request("POST", "/equipment/1/jobs", {
            "method": "ftp", "schedule_type": "daily", "schedule_time": "02:30",
            "timezone": "America/Sao_Paulo",
        })
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/equipment/1")
        with connect() as conn:
            job = conn.execute("SELECT method,schedule_enabled,schedule_type,schedule_time FROM backup_jobs WHERE equipment_id=1 ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(tuple(job), ("ftp", 1, "daily", "02:30"))

    def test_olt_manual_script_uses_linked_ftp_account_without_cache(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='vsol_olt_ssh_ftp' WHERE id=1")
            conn.execute("UPDATE settings SET value='192.0.2.90' WHERE key='ftp_public_ip'")
            conn.execute(
                """INSERT INTO ftp_accounts(uuid,equipment_id,name,username,password_hash_or_secret_reference,
                   home_relative_path,is_active,sync_status,control_port,permission_mode)
                   VALUES('olt-account',1,'OLT FTP','olt_user',?,'ftp/olt',1,'synced',2121,'upload_only')""",
                ("fernet:" + encrypt_secret("FtpSecret!1"),),
            )
            conn.commit()
        csrf = self.settings_csrf()
        status, headers, page = self.client.request("POST", "/equipment/1/olt-script", {"action": "manual", "csrf_token": csrf})
        self.assertTrue(status.startswith("201"))
        self.assertIn("copy startup-config ftp://olt_user:FtpSecret%211@192.0.2.90:2121/", page)
        self.assertIn("Fechar aba", page)
        self.assertIn("no-store", headers.get("Cache-Control", ""))

    def test_olt_execute_uses_persistent_vendor_plan(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='cdata_olt_ssh_ftp' WHERE id=1")
            conn.execute("UPDATE settings SET value='192.0.2.90' WHERE key='ftp_public_ip'")
            conn.execute(
                """INSERT INTO ftp_accounts(uuid,equipment_id,name,username,password_hash_or_secret_reference,
                   home_relative_path,is_active,sync_status,control_port,permission_mode)
                   VALUES('olt-exec-account',1,'OLT FTP','olt_user',?,'ftp/olt',1,'synced',21,'upload_only')""",
                ("fernet:" + encrypt_secret("FtpSecret!1"),),
            )
            conn.execute(
                """INSERT INTO device_credentials(uuid,equipment_id,credential_type,name,username,password_encrypted,
                   port,auth_method,is_active) VALUES('olt-exec-cred',1,'ssh','OLT SSH','admin',?,22,'password',1)""",
                (encrypt_secret("DeviceSecret!1"),),
            )
            conn.commit()
        fake_client = mock.Mock()
        csrf = self.settings_csrf()
        with mock.patch("backup_manager.olt_runtime.connect_device", return_value=fake_client), \
             mock.patch("backup_manager.olt_runtime.run_ssh_plan", return_value={"ok": True, "expected_files": ("olt.gz",)}):
            status, headers, _ = self.client.request("POST", "/equipment/1/olt-execute", {"action": "manual", "csrf_token": csrf})
        self.assertTrue(status.startswith("302"), status)
        self.assertIn("/equipment/1", headers.get("Location", ""))
        fake_client.close.assert_called_once()
        with connect() as conn:
            row = conn.execute("SELECT details FROM audit_log WHERE action='olt.backup_triggered' ORDER BY id DESC").fetchone()
            operation = conn.execute("SELECT * FROM backup_operations WHERE provider_key='olt_ftp' ORDER BY id DESC").fetchone()
            artifacts = conn.execute("SELECT * FROM backup_operation_artifacts WHERE operation_id=?", (operation["id"],)).fetchall()
        self.assertIsNotNone(row)
        self.assertNotIn("FtpSecret!1", row["details"])
        self.assertEqual("waiting_upload", operation["status"])
        self.assertEqual(1, len(artifacts))
        with connect() as conn:
            became_success = complete_olt_received_file(
                conn, equipment_id=1, filename=artifacts[0]["filename"], backup_id=None,
                size_bytes=128, sha256="a" * 64,
            )
            final_status = conn.execute("SELECT status FROM backup_operations WHERE id=?", (operation["id"],)).fetchone()[0]
        self.assertTrue(became_success)
        self.assertEqual("success", final_status)

    def test_olt_actions_reject_missing_csrf_token(self) -> None:
        self.login_ready_admin()
        status, _, page = self.client.request("POST", "/equipment/1/olt-execute", {"action": "manual"})
        self.assertTrue(status.startswith("403"))
        self.assertIn("Token CSRF inválido", page)

    def test_login_requires_password_change_then_setup_and_crud(self) -> None:
        status, _, body = self.client.request("GET", "/login")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Backup Manager Local", body)

        status, headers, _ = self.client.request(
            "POST",
            "/login",
            {"username": "admin", "password": TEMP_ADMIN_PASSWORD},
        )
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/change-password")

        status, headers, _ = self.client.request("GET", "/")
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/change-password")

        status, _, password_page = self.client.request("GET", "/change-password")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Defina sua nova senha de acesso", password_page)
        self.assertIn("Atualize sua senha", password_page)
        self.assertIn("Mantenha sua conta", password_page)
        self.assertIn("Mínimo de", password_page)
        self.assertIn("data-password-strength", password_page)
        self.assertIn("data-password-toggle", password_page)
        self.assertIn("Sair e fazer login depois", password_page)
        self.assertIn('autocomplete="current-password"', password_page)
        self.assertIn('autocomplete="new-password"', password_page)

        status, headers, _ = self.client.request(
            "POST",
            "/change-password",
            {
                "current_password": TEMP_ADMIN_PASSWORD,
                "new_password": "NovaSenha!2026",
                "confirm_password": "NovaSenha!2026",
            },
        )
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/setup")

        status, _, body = self.client.request("GET", "/setup")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Configuração inicial", body)
        self.assertIn('class="setup-modern-page"', body)
        self.assertIn('class="wizard setup-modern-form"', body)

        status, headers, _ = self.client.request(
            "POST",
            "/setup",
            {
                "installation_name": "Backup Manager Lab",
                "provider_name": "Provedor Local",
                "timezone": "America/Sao_Paulo",
                "storage_root": os.environ["BACKUP_MANAGER_STORAGE_ROOT"],
                "primary_environment": "Producao",
                "pops": "POP Principal, Sao Paulo, SP\nPOP Norte, Manaus, AM",
            },
        )
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/")

        status, _, body = self.client.request("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Backup Manager Lab", body)

        status, headers, _ = self.client.request(
            "POST",
            "/equipment",
            {
                "name": "Core Router",
                "hostname": "router-core-01",
                "ip_address": "10.0.0.1",
                "vendor_id": "3",
                "group_id": "1",
                "pop_id": "1",
                "environment_id": "1",
                "is_active": "1",
                "ssh_backup_driver": "huawei_vrp",
                "ssh_port": "2222",
                "ssh_custom_command": "",
                "notes": "Cadastro de teste",
            },
        )
        self.assertTrue(status.startswith("201"))
        self.assertIn("Continuar configuração do equipamento Huawei", _)
        self.assertIn("Validar SSH e continuar", _)
        for forbidden in ("MikroTik", "RouterOS", "FTP Push"):
            self.assertNotIn(forbidden, _)

        status, _, body = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200"))
        self.assertIn("router-core-01", body)
        with connect() as conn:
            equipment = conn.execute("SELECT name, ssh_backup_driver, ssh_port FROM equipment WHERE hostname = 'router-core-01'").fetchone()
        self.assertEqual(equipment["name"], "router-core-01")
        self.assertEqual(equipment["ssh_backup_driver"], "huawei_vrp")
        self.assertEqual(equipment["ssh_port"], 2222)

        self.assertEqual(integrity_check(), "ok")

    def test_first_access_password_page_matches_reference_and_enforces_strength(self) -> None:
        status, headers, _ = self.client.request("POST", "/login", {
            "username": "admin", "password": TEMP_ADMIN_PASSWORD,
        })
        self.assertTrue(status.startswith("302"))
        self.assertEqual("/change-password", headers["Location"])
        status, _, page = self.client.request("GET", "/change-password")
        self.assertTrue(status.startswith("200"))
        for expected in ("first-access-page", "Defina sua nova senha de acesso", "Atualize sua senha",
                         "Força da senha", "Mantenha sua conta", "Boas práticas",
                         "Sair e fazer login depois", "data-password-toggle"):
            self.assertIn(expected, page)
        status, _, weak = self.client.request("POST", "/change-password", {
            "current_password": TEMP_ADMIN_PASSWORD,
            "new_password": "senhasimples",
            "confirm_password": "senhasimples",
        })
        self.assertTrue(status.startswith("200"))
        self.assertIn("uma letra maiúscula, um número e um caractere especial", weak)
        status, headers, _ = self.client.request("POST", "/logout")
        self.assertTrue(status.startswith("302"))
        self.assertEqual("/login", headers["Location"])

    def test_equipment_creation_is_guided_ipv4_only_and_ftp_is_preselected(self) -> None:
        self.login_ready_admin()
        status, _, page = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Nome do dispositivo (hostname)", page)
        self.assertIn("Endereço IPv4", page)
        self.assertIn("Método de conexão", page)
        for removed in ("IP ou host", "Comando customizado", "Operação SSH", "Tipo/driver de backup SSH"):
            self.assertNotIn(removed, page)
        self.assertNotIn('name="is_active"', page)
        self.assertNotIn('<option value="">Nenhum</option>', page)
        self.assertIn('href="/equipment?new=1"', page)
        self.assertIn('<dialog id="equipment-create"', page)
        self.assertNotIn('data-dialog-auto-open="true"', page)

        form = {"hostname": "switch-core-01", "ip_address": "equipamento.local", "vendor_id": "1",
                "ssh_backup_driver": "cisco_ios", "ssh_port": "22"}
        status, _, invalid = self.client.request("POST", "/equipment", form)
        self.assertTrue(status.startswith("200")); self.assertIn("endereço IPv4 válido", invalid)
        self.assertIn('data-dialog-auto-open="true"', invalid)
        with connect() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM equipment WHERE hostname='switch-core-01'").fetchone())

        form["ip_address"] = "192.168.0.10"
        status, _, created = self.client.request("POST", "/equipment", form)
        self.assertTrue(status.startswith("201")); self.assertIn("Próximo passo", created)
        with connect() as conn:
            row = conn.execute("SELECT * FROM equipment WHERE hostname='switch-core-01'").fetchone()
        self.assertEqual(row["name"], "switch-core-01"); self.assertEqual(row["is_active"], 1)
        self.assertEqual(row["ssh_backup_driver"], "cisco_ios"); self.assertEqual(row["ssh_custom_command"], "")
        status, _, ftp = self.client.request("GET", f"/ftp?new=1&equipment_id={row['id']}")
        self.assertTrue(status.startswith("200")); self.assertIn(f'type="hidden" name="equipment_id" value="{row["id"]}"', ftp)
        self.assertIn('value="switch-core-01" disabled', ftp)
        self.assertNotIn("Selecione um equipamento", ftp)
        self.assertIn('data-label="Receber backup de um equipamento"', ftp)
        self.assertIn('type="submit" data-ftp-submit hidden>Concluir cadastro', ftp)
        self.assertNotIn('ftp-review-submit', ftp)
        self.assertIn('id="ftp-name-field" hidden', ftp); self.assertNotIn('name="is_active"', ftp)

        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, _, _ = self.client.request("POST", "/ftp", {"equipment_id": str(row["id"]),
                "username": "switchftp", "password": "FtpPassword!1", "confirm_password": "FtpPassword!1",
                "permission_mode": "upload_only", "notes": ""})
        self.assertTrue(status.startswith("201"))
        with connect() as conn:
            account = conn.execute("SELECT * FROM ftp_accounts WHERE equipment_id=?", (row["id"],)).fetchone()
        self.assertEqual(account["name"], "FTP · switch-core-01"); self.assertEqual(account["is_active"], 1)

    def test_mikrotik_onboarding_branches_after_ssh_validation(self) -> None:
        self.login_ready_admin()
        status, _, wizard = self.client.request("GET", "/equipment?new=1")
        self.assertTrue(status.startswith("200"))
        for text in ("Escolha o método do backup", "Backup por FTP Push", "Backup via SSH",
                     "agendamento e envia os arquivos", "executa e agenda o backup",
                     "Mínimo de 5 caracteres; recomendamos 10 ou mais"):
            self.assertIn(text, wizard)
        self.assertIn('name="ssh_password" type="password" autocomplete="new-password" minlength="5"', wizard)
        self.assertIn('type="radio" name="mikrotik_backup_mode" value="ftp" checked', wizard)
        self.assertIn('type="radio" name="mikrotik_backup_mode" value="ssh"', wizard)
        status, _, stylesheet = self.client.request("GET", "/static/app.css")
        self.assertTrue(status.startswith("200"))
        self.assertIn(".equipment-job-dialog #criar-job { max-height: calc(100vh - 32px);", stylesheet)
        self.assertIn("overflow-y: auto", stylesheet)
        weak_form = {
            "hostname": "mk-weak-password", "ip_address": "192.0.2.80", "vendor_id": "4",
            "group_id": "1", "pop_id": "1", "environment_id": "1",
            "ssh_backup_driver": "mikrotik_routeros", "ssh_port": "22", "ssh_username": "backup",
            "ssh_password": "1234", "ssh_confirm_password": "1234",
        }
        status, _, rejected = self.client.request("POST", "/equipment", weak_form)
        self.assertTrue(status.startswith("200"))
        self.assertIn("pelo menos 5 caracteres", rejected)
        with connect() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM equipment WHERE hostname='mk-weak-password'").fetchone())
        base = {
            "ip_address": "192.0.2.81", "vendor_id": "4", "group_id": "1", "pop_id": "1",
            "environment_id": "1", "ssh_backup_driver": "mikrotik_routeros", "ssh_port": "22",
            "ssh_username": "backup", "ssh_password": "StrongSSH!1", "ssh_confirm_password": "StrongSSH!1",
        }
        for mode, hostname in (("ssh", "mk-onboarding-ssh"), ("ftp", "mk-onboarding-ftp")):
            form = {**base, "hostname": hostname, "mikrotik_backup_mode": mode}
            status, _, created = self.client.request("POST", "/equipment", form)
            self.assertTrue(status.startswith("201"))
            with connect() as conn:
                equipment_id = conn.execute("SELECT id FROM equipment WHERE hostname=?", (hostname,)).fetchone()[0]
            self.assertIn(f'/equipment/{equipment_id}/ssh-test?onboarding={mode}', created)
            with mock.patch("backup_manager.app.probe_connection", return_value="mikrotik_routeros"):
                status, headers, _ = self.client.request("POST", f"/equipment/{equipment_id}/ssh-test?onboarding={mode}")
            self.assertTrue(status.startswith("302"))
            self.assertEqual(f"/equipment/{equipment_id}?onboarding={mode}", headers["Location"])
            status, _, page = self.client.request("GET", headers["Location"])
            self.assertTrue(status.startswith("200"))
            if mode == "ssh":
                self.assertIn('id="equipment-job-create" class="equipment-job-dialog" data-dialog-auto-open', page)
                self.assertIn("SSH validado com sucesso", page)
                self.assertIn("executará o primeiro backup SSH e mostrará o resultado", page)
                self.assertIn('<option value="ssh" selected', page)
                self.assertIn('<option value="daily" selected', page)
                self.assertIn('name="onboarding" value="ssh"', page)
                status, headers, _ = self.client.request("POST", f"/equipment/{equipment_id}/jobs", {
                    "method": "ssh", "schedule_type": "daily", "schedule_time": "02:00",
                    "timezone": "America/Sao_Paulo", "onboarding": "ssh",
                })
                self.assertTrue(status.startswith("302"))
                self.assertRegex(headers["Location"], rf"^/equipment/{equipment_id}\?run=[0-9a-f-]{{36}}$")
                with connect() as conn:
                    job = conn.execute("SELECT method,schedule_type,schedule_enabled FROM backup_jobs WHERE equipment_id=?", (equipment_id,)).fetchone()
                    run = conn.execute("SELECT status,trigger_type FROM backup_job_runs WHERE equipment_id=?", (equipment_id,)).fetchone()
                self.assertEqual((job["method"], job["schedule_type"], job["schedule_enabled"]), ("ssh", "daily", 1))
                self.assertEqual(run["trigger_type"], "manual")
            else:
                self.assertIn('data-mikrotik-config-wizard', page)
                self.assertNotIn('data-mikrotik-config-wizard data-dialog-auto-open', page)
                self.assertIn('name="onboarding" value="ftp"', page)
                with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
                    status, headers, _ = self.client.request("POST", f"/equipment/{equipment_id}/mikrotik-ftp", {
                        "onboarding": "ftp", "credential_action": "isolated", "routeros_version": "7",
                        "backup_format": "both", "schedule_mode": "scheduled", "schedule_time": "02:00",
                        "ftp_host": "192.0.2.10", "ftp_port": "21", "ftp_directory": "/",
                    })
                self.assertTrue(status.startswith("302"))
                self.assertIn(f"/equipment/{equipment_id}?onboarding=ftp-install", headers["Location"])
                status, _, install_page = self.client.request("GET", headers["Location"])
                self.assertTrue(status.startswith("200"))
                self.assertIn('id="mikrotik-install-wizard"', install_page)
                self.assertIn('data-dialog-auto-open', install_page.split('id="mikrotik-install-wizard"', 1)[1].split(">", 1)[0])

    def test_equipment_vendor_driver_compatibility_rejects_tampered_posts(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            huawei = conn.execute("SELECT id FROM vendors WHERE name='Huawei'").fetchone()[0]
            mikrotik = conn.execute("SELECT id FROM vendors WHERE name='MikroTik'").fetchone()[0]
        base = {"ip_address": "192.0.2.90", "ssh_port": "22"}
        cases = (
            ({**base, "hostname": "bad-huawei-mikrotik", "vendor_id": str(huawei),
              "ssh_backup_driver": "mikrotik_routeros"}, "não é compatível"),
            ({**base, "hostname": "bad-mikrotik-huawei", "vendor_id": str(mikrotik),
              "ssh_backup_driver": "huawei_vrp"}, "não é compatível"),
            ({**base, "hostname": "bad-vendor", "vendor_id": "999999",
              "ssh_backup_driver": "generic_ssh"}, "Fabricante inválido"),
        )
        for form, expected in cases:
            with self.subTest(hostname=form["hostname"]):
                status, _, page = self.client.request("POST", "/equipment", form)
                self.assertTrue(status.startswith("200"))
                self.assertIn(expected, page)
                with connect() as conn:
                    self.assertIsNone(conn.execute(
                        "SELECT id FROM equipment WHERE hostname=?", (form["hostname"],)
                    ).fetchone())

    def test_huawei_router_and_olt_receive_their_own_safe_next_steps(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            huawei = conn.execute("SELECT id FROM vendors WHERE name='Huawei'").fetchone()[0]
        cases = (
            ("huawei-router-safe", "huawei_router", "ssh-test?onboarding=ssh", "Valide o acesso SSH"),
            ("huawei-olt-safe", "huawei_olt_ssh_ftp", "#backup", "configuração da Huawei"),
        )
        for hostname, driver, target, expected in cases:
            with self.subTest(driver=driver):
                status, _, page = self.client.request("POST", "/equipment", {
                    "hostname": hostname, "ip_address": "192.0.2.92", "vendor_id": str(huawei),
                    "ssh_backup_driver": driver, "ssh_port": "22",
                })
                self.assertTrue(status.startswith("201"))
                self.assertIn(target, page)
                self.assertIn(expected, page)
                for forbidden in ("MikroTik", "RouterOS", "FTP Push"):
                    self.assertNotIn(forbidden, page)

    def test_legacy_huawei_mikrotik_integration_is_blocked_before_effects(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            huawei = conn.execute("SELECT id FROM vendors WHERE name='Huawei'").fetchone()[0]
            mikrotik = conn.execute("SELECT id FROM vendors WHERE name='MikroTik'").fetchone()[0]
        form = {
            "hostname": "legacy-mismatch", "ip_address": "192.0.2.91", "vendor_id": str(mikrotik),
            "ssh_backup_driver": "mikrotik_routeros", "ssh_port": "22", "ssh_username": "backup",
            "ssh_password": "StrongSSH!1", "ssh_confirm_password": "StrongSSH!1",
        }
        status, _, _ = self.client.request("POST", "/equipment", form)
        self.assertTrue(status.startswith("201"))
        with connect() as conn:
            equipment_id = conn.execute("SELECT id FROM equipment WHERE hostname='legacy-mismatch'").fetchone()[0]
        common = {"credential_action": "isolated", "routeros_version": "7", "backup_format": "both",
                  "schedule_mode": "scheduled", "schedule_time": "02:00", "ftp_host": "192.0.2.10",
                  "ftp_port": "21", "ftp_directory": "/"}
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, _, _ = self.client.request("POST", f"/equipment/{equipment_id}/mikrotik-ftp", common)
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            integration_id = conn.execute(
                "SELECT id FROM mikrotik_ftp_integrations WHERE equipment_id=?", (equipment_id,)
            ).fetchone()[0]
            conn.execute("UPDATE equipment SET vendor_id=? WHERE id=?", (huawei, equipment_id))

        with mock.patch("backup_manager.app.run_routeros_ftp_test") as ftp_test, \
             mock.patch("backup_manager.app.install_routeros_script") as install, \
             mock.patch("backup_manager.app.run_routeros_managed_backup") as execute, \
             mock.patch("backup_manager.app.remove_routeros_backup_manager") as remove, \
             mock.patch("backup_manager.app.generate_mikrotik_install_script") as generate, \
             mock.patch("backup_manager.app.run_helper") as helper:
            paths = (
                f"/mikrotik-ftp/{integration_id}/test",
                f"/mikrotik-ftp/{integration_id}/install-ssh",
                f"/mikrotik-ftp/{integration_id}/repair-ssh",
                f"/mikrotik-ftp/{integration_id}/execute-ssh",
                f"/mikrotik-ftp/{integration_id}/script",
                f"/mikrotik-ftp/{integration_id}/rotate",
                f"/mikrotik-ftp/{integration_id}/remove-ssh",
                f"/mikrotik-ftp/{integration_id}/deactivate",
            )
            for path in paths:
                status, _, _ = self.client.request("POST", path, headers={"Accept": "application/json"})
                self.assertIn(status.split()[0], {"302", "400", "404"})
            for effect in (ftp_test, install, execute, remove, generate, helper):
                effect.assert_not_called()

    def test_legacy_inconsistent_equipment_can_be_corrected_by_editing(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            huawei = conn.execute("SELECT id FROM vendors WHERE name='Huawei'").fetchone()[0]
            mikrotik = conn.execute("SELECT id FROM vendors WHERE name='MikroTik'").fetchone()[0]
            equipment_id = conn.execute(
                """INSERT INTO equipment(name,hostname,ip_address,vendor_id,is_active,ssh_backup_driver,ssh_port)
                   VALUES('Legado divergente','legacy-edit-fix','192.0.2.93',?,1,'mikrotik_routeros',22)""",
                (huawei,),
            ).lastrowid
        status, _, detail = self.client.request("GET", f"/equipment/{equipment_id}")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Cadastro inconsistente", detail)
        status, headers, _ = self.client.request("POST", f"/equipment/{equipment_id}/edit", {
            "hostname": "legacy-edit-fix", "ip_address": "192.0.2.93", "vendor_id": str(mikrotik),
            "ssh_backup_driver": "mikrotik_routeros", "ssh_port": "22", "is_active": "1",
            "preserve_equipment_state": "1", "preserve_custom_command": "1",
        })
        self.assertTrue(status.startswith("302"))
        self.assertEqual("/equipment", headers["Location"].split("?", 1)[0])
        with connect() as conn:
            corrected = conn.execute(
                """SELECT vendors.name AS vendor,equipment.ssh_backup_driver FROM equipment
                   JOIN vendors ON vendors.id=equipment.vendor_id WHERE equipment.id=?""", (equipment_id,)
            ).fetchone()
        self.assertEqual((corrected["vendor"], corrected["ssh_backup_driver"]),
                         ("MikroTik", "mikrotik_routeros"))

    def test_standalone_ftp_account_is_created_without_equipment_and_visible_in_ui(self) -> None:
        self.login_ready_admin()
        status, _, page = self.client.request("GET", "/ftp?new=1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Servidor de arquivos independente", page)
        form = {"account_type": "file_server", "name": "Atualizações das OLTs",
                "equipment_id": "", "username": "oltupdates", "password": "FtpPassword!1",
                "confirm_password": "FtpPassword!1", "permission_mode": "upload_only", "notes": ""}
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "account_synced")):
            status, _, created = self.client.request("POST", "/ftp", form)
        self.assertTrue(status.startswith("201"))
        self.assertNotIn("Configurar no equipamento", created)
        with connect() as conn:
            account = conn.execute("SELECT * FROM ftp_accounts WHERE username='oltupdates'").fetchone()
            self.assertIsNone(account["equipment_id"])
            self.assertEqual("file_server", account["account_type"])
            standalone_file = Path(os.environ["BACKUP_MANAGER_STORAGE_ROOT"]) / account["home_relative_path"] / "olt-firmware.bin"
            standalone_file.write_bytes(b"firmware")
            scan_once(conn)
            self.assertTrue(standalone_file.is_file())
            self.assertEqual(0, conn.execute(
                "SELECT COUNT(*) FROM ftp_received_files WHERE ftp_account_id=?", (account["id"],)
            ).fetchone()[0])
        status, _, listing = self.client.request("GET", "/ftp")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Atualizações das OLTs", listing)
        self.assertIn("Independente", listing)
        status, _, detail = self.client.request("GET", f"/ftp/{account['id']}")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Sem vínculo", detail)

    def test_ftp_account_lifecycle_releases_identity_preserves_history_and_rolls_back_sync_failures(self) -> None:
        self.login_ready_admin()
        equipment_id = self.create_equipment_row("ftp-lifecycle", "mikrotik_routeros")
        form = {"equipment_id": str(equipment_id), "username": "lifecycleftp",
                "password": "FtpPassword!1", "confirm_password": "FtpPassword!1",
                "permission_mode": "upload_only", "notes": ""}
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, _, _ = self.client.request("POST", "/ftp", form)
        self.assertTrue(status.startswith("201"))
        with connect() as conn:
            original = conn.execute("SELECT * FROM ftp_accounts WHERE equipment_id=? AND is_active=1", (equipment_id,)).fetchone()
            conn.execute("""INSERT INTO ftp_received_files(uuid,ftp_account_id,equipment_id,original_filename,
                incoming_relative_path,status,file_size) VALUES(?,?,?,?,?,'imported',42)""",
                (str(uuid.uuid4()), original["id"], equipment_id, "historical.backup", "incoming/historical.backup"))

        status, _, duplicate = self.client.request("POST", "/ftp", form)
        self.assertTrue(status.startswith("200")); self.assertIn("Usuario ou equipamento ja possui conta FTP", duplicate)

        with mock.patch("backup_manager.app.run_helper", return_value=(False, "pure_pw_failed")):
            status, _, _ = self.client.request("POST", f"/ftp/{original['id']}/toggle")
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            self.assertEqual(1, conn.execute("SELECT is_active FROM ftp_accounts WHERE id=?", (original["id"],)).fetchone()[0])
            self.assertEqual("ftp.account_disable_failed", conn.execute("SELECT action FROM audit_log ORDER BY id DESC").fetchone()[0])

        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            self.client.request("POST", f"/ftp/{original['id']}/toggle")
            status, _, _ = self.client.request("POST", "/ftp", form)
        self.assertTrue(status.startswith("201"))
        with connect() as conn:
            replacement = conn.execute("SELECT * FROM ftp_accounts WHERE equipment_id=? AND is_active=1", (equipment_id,)).fetchone()
            self.assertNotEqual(original["id"], replacement["id"])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE ftp_account_id=?", (original["id"],)).fetchone()[0])

        with mock.patch("backup_manager.app.run_helper", return_value=(False, "puredb_failed")):
            self.client.request("POST", f"/ftp/{replacement['id']}/delete")
        with connect() as conn:
            rolled_back = conn.execute("SELECT * FROM ftp_accounts WHERE id=?", (replacement["id"],)).fetchone()
            self.assertEqual(1, rolled_back["is_active"]); self.assertIsNone(rolled_back["deleted_at"])

        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            self.client.request("POST", f"/ftp/{replacement['id']}/delete")
            status, _, _ = self.client.request("POST", "/ftp", form)
        self.assertTrue(status.startswith("201"))
        with connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT deleted_at FROM ftp_accounts WHERE id=?", (replacement["id"],)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE ftp_account_id=?", (original["id"],)).fetchone()[0])
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            self.client.request("POST", f"/ftp/{original['id']}/delete")
        status, _, archived = self.client.request("GET", "/ftp?lifecycle=deleted")
        self.assertTrue(status.startswith("200")); self.assertIn("Excluídas", archived)
        self.assertIn("Ver histórico", archived); self.assertIn("lifecycleftp", archived)
        status, _, history = self.client.request("GET", f"/ftp/{original['id']}")
        self.assertTrue(status.startswith("200")); self.assertIn("Conta excluída", history)
        self.assertIn("arquivos e histórico foram preservados", history)
        self.assertIn("historical.backup", history); self.assertIn("Somente histórico", history)

    def test_equipment_delete_modal_confirmation_auth_and_success(self) -> None:
        self.login_ready_admin()
        equipment_id = self.create_equipment_row("delete-empty")
        status, _, listing = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200")); self.assertIn(f'id="equipment-delete-{equipment_id}"', listing)
        self.assertIn("delete-empty", listing); self.assertIn("192.0.2.99", listing)
        self.assertIn("Nenhuma dependência encontrada", listing)
        self.assertIn('name="confirm_name"', listing); self.assertIn("Digite <strong>delete-empty</strong>", listing); self.assertIn("Cancelar", listing)

        status, _, missing = self.client.request("POST", "/equipment/delete", {"id": str(equipment_id)})
        self.assertTrue(status.startswith("200")); self.assertIn("Confirme explicitamente", missing)
        self.assertIn("app-shell", missing); self.assertNotIn("FOREIGN KEY", missing); self.assertNotIn("Traceback", missing)
        with connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM equipment WHERE id=?", (equipment_id,)).fetchone())
            self.assertEqual("equipment.delete_blocked", conn.execute("SELECT action FROM audit_log ORDER BY id DESC").fetchone()[0])

        anonymous = WsgiClient()
        status, headers, _ = anonymous.request("POST", "/equipment/delete", {"id": str(equipment_id), "confirm": "1", "confirm_name": "delete-empty"})
        self.assertTrue(status.startswith("302")); self.assertEqual("/login", headers["Location"])
        with connect() as conn:
            conn.execute("INSERT INTO users(username,full_name,password_hash,role_id,must_change_password) VALUES(?,?,?,(SELECT id FROM roles WHERE slug='read_download'),0)",
                         ("deleteviewer", "Viewer", hash_password("ViewerDelete!1")))
        viewer = WsgiClient(); viewer.request("POST", "/login", {"username": "deleteviewer", "password": "ViewerDelete!1"})
        status, _, _ = viewer.request("POST", "/equipment/delete", {"id": str(equipment_id), "confirm": "1", "confirm_name": "delete-empty"})
        self.assertTrue(status.startswith("403"))

        status, headers, _ = self.client.request("POST", "/equipment/delete", {"id": str(equipment_id), "confirm": "1", "confirm_name": "delete-empty"})
        self.assertTrue(status.startswith("302")); self.assertEqual("/equipment", headers["Location"].split("?", 1)[0])
        with connect() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM equipment WHERE id=?", (equipment_id,)).fetchone())
            self.assertEqual("equipment.deleted", conn.execute("SELECT action FROM audit_log ORDER BY id DESC").fetchone()[0])
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_equipment_delete_blocks_backups_jobs_schedules_credentials_and_rolls_back(self) -> None:
        self.login_ready_admin()
        equipment_id = self.create_equipment_row("delete-history")
        self.client.multipart(f"/equipment/{equipment_id}/backups/import", {}, "history.cfg", b"historical configuration")
        with connect() as conn:
            for schedule_type in ("none", "daily"):
                conn.execute("""INSERT INTO backup_jobs(uuid,equipment_id,job_type,method,schedule_enabled,schedule_type,
                    schedule_time,timezone,status) VALUES(?,?, 'manual','dry_run',?,?,?,'America/Sao_Paulo','active')""",
                    (str(uuid.uuid4()), equipment_id, int(schedule_type != "none"), schedule_type, "02:00" if schedule_type != "none" else ""))
            create_credential(conn, equipment_id, 1, "SSH", "backup", "SenhaSSH!2026", 22, "")
        status, _, blocked = self.client.request("POST", "/equipment/delete", {"id": str(equipment_id), "confirm": "1", "confirm_name": "delete-history"})
        self.assertTrue(status.startswith("200")); self.assertIn("dependências históricas preservadas", blocked)
        for text in ("1 backups registrados", "2 agendamentos", "1 credenciais SSH", "Desativar equipamento"):
            self.assertIn(text, blocked)
        with connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM equipment WHERE id=?", (equipment_id,)).fetchone())
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE equipment_id=?", (equipment_id,)).fetchone()[0])

        empty_id = self.create_equipment_row("delete-rollback")
        with mock.patch("backup_manager.app.audit", side_effect=RuntimeError("forced transaction failure")):
            status, _, failed = self.client.request("POST", "/equipment/delete", {"id": str(empty_id), "confirm": "1", "confirm_name": "delete-rollback"})
        self.assertTrue(status.startswith("200")); self.assertIn("Ocorrência", failed); self.assertIn("app-shell", failed)
        self.assertNotIn("forced transaction failure", failed); self.assertNotIn("DELETE FROM", failed)
        with connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM equipment WHERE id=?", (empty_id,)).fetchone())
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_equipment_purge_reports_and_removes_all_dependencies(self) -> None:
        self.login_ready_admin()
        equipment_id = self.create_equipment_row("purge-complete")
        self.client.multipart(f"/equipment/{equipment_id}/backups/import", {}, "purge.cfg", b"configuration")
        with connect() as conn:
            create_credential(conn, equipment_id, 1, "SSH", "backup", "SenhaSSH!2026", 22, "")
            conn.execute("""INSERT INTO backup_jobs(uuid,equipment_id,job_type,method,schedule_enabled,schedule_type,
                schedule_time,timezone,status) VALUES(?,?, 'manual','dry_run',0,'none','','America/Sao_Paulo','active')""",
                         (str(uuid.uuid4()), equipment_id))
            conn.execute("UPDATE equipment SET is_active=0 WHERE id=?", (equipment_id,))
        status, _, listing = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200"))
        modal = listing.split(f'id="equipment-delete-{equipment_id}"', 1)[1].split("</dialog>", 1)[0]
        self.assertIn(f'id="equipment-delete-{equipment_id}" class="equipment-delete-dialog"', listing)
        for expected in ("Gerenciar ciclo de vida", "Itens vinculados", "Desativar", "Arquivar", "Excluir definitivamente"):
            self.assertIn(expected, modal)
        for expected in ("1 backups registrados", "1 agendamentos", "1 credenciais SSH",
                         "EXCLUIR TUDO", "Remover equipamento e tudo atrelado"):
            self.assertIn(expected, modal)
        status, _, _ = self.client.request("POST", f"/equipment/{equipment_id}/purge",
                                           {"confirm_name": "purge-complete", "confirmation": "EXCLUIR TUDO"})
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM equipment WHERE id=?", (equipment_id,)).fetchone())
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM backups WHERE equipment_id=?", (equipment_id,)).fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE equipment_id=?", (equipment_id,)).fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM device_credentials WHERE equipment_id=?", (equipment_id,)).fetchone()[0])
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_equipment_delete_explains_ftp_accounts_push_tests_uploads_and_deactivation(self) -> None:
        self.login_ready_admin()
        active_id = self.create_equipment_row("delete-ftp-active", "mikrotik_routeros")
        inactive_id = self.create_equipment_row("delete-ftp-inactive", "mikrotik_routeros")
        with connect() as conn:
            active_account = create_account(conn, equipment_id=active_id, name="FTP ativa", username="deleteactive", password="DeleteFtp!1")
            inactive_account = create_account(conn, equipment_id=inactive_id, name="FTP inativa", username="deleteinactive", password="DeleteFtp!2", is_active=False)
            integration_id = conn.execute("""INSERT INTO mikrotik_ftp_integrations(uuid,equipment_id,ftp_account_id,
                routeros_major_version,backup_format,schedule_mode,schedule_frequency,schedule_time,ftp_host,ftp_port,
                ftp_directory,encrypted_ftp_secret,is_active) VALUES(?,?,?,7,'backup','manual','daily','02:00','192.0.2.1',21,'/','',1)""",
                (str(uuid.uuid4()), active_id, active_account["id"])).lastrowid
            test_id = conn.execute("""INSERT INTO mikrotik_ftp_tests(uuid,integration_id,token_hash,status,expires_at)
                VALUES(?,?,?,'waiting_upload','2099-01-01 00:00:00')""", (str(uuid.uuid4()), integration_id, uuid.uuid4().hex)).lastrowid
            conn.execute("""INSERT INTO mikrotik_ftp_uploads(uuid,integration_id,equipment_id,test_id,original_filename,
                file_type,routeros_major_version,backup_format) VALUES(?,?,?,?,?,'backup',7,'backup')""",
                (str(uuid.uuid4()), integration_id, active_id, test_id, "device-test.backup"))
            conn.execute("""INSERT INTO ftp_received_files(uuid,ftp_account_id,equipment_id,original_filename,
                incoming_relative_path,status,file_size) VALUES(?,?,?,?,?,'imported',10)""",
                (str(uuid.uuid4()), active_account["id"], active_id, "legacy.backup", "incoming/legacy.backup"))
            conn.execute("UPDATE ftp_accounts SET last_upload_at=CURRENT_TIMESTAMP WHERE id=?", (active_account["id"],))
            conn.execute("""INSERT INTO mikrotik_ftp_integrations(uuid,equipment_id,ftp_account_id,
                routeros_major_version,backup_format,schedule_mode,schedule_frequency,schedule_time,ftp_host,ftp_port,
                ftp_directory,encrypted_ftp_secret,is_active) VALUES(?,?,?,7,'backup','manual','daily','02:00','192.0.2.1',21,'/','',0)""",
                (str(uuid.uuid4()), inactive_id, inactive_account["id"]))
        status, _, active = self.client.request("POST", "/equipment/delete", {"id": str(active_id), "confirm": "1", "confirm_name": "delete-ftp-active"})
        self.assertTrue(status.startswith("200")); self.assertIn("Desative-a antes de remover este equipamento", active)
        for text in ("1 contas FTP", "1 integrações FTP Push", "1 testes FTP Push", "1 uploads FTP Push",
                     "Conta FTP #", "<strong>ativa</strong>", "com histórico", "vinculada ao FTP Push", "Gerenciar backup FTP",
                     "Desativar integração FTP Push"):
            self.assertIn(text, active)
        status, _, inactive = self.client.request("POST", "/equipment/delete", {"id": str(inactive_id), "confirm": "1", "confirm_name": "delete-ftp-inactive"})
        self.assertTrue(status.startswith("200")); self.assertIn("Conta FTP #", inactive)
        inactive_modal = inactive.split(f'id="equipment-delete-{inactive_id}"', 1)[1].split("</dialog>", 1)[0]
        self.assertIn("vinculada ao FTP Push", inactive_modal)
        self.assertIn("Integração FTP Push #", inactive_modal)
        self.assertIn("<strong>inativa</strong>", inactive_modal)
        self.assertNotIn("Desativar integração FTP Push</button>", inactive_modal)
        status, headers, _ = self.client.request("POST", f"/equipment/{active_id}/archive",
            {"confirm_name": "delete-ftp-active", "file_action": "trash"})
        self.assertTrue(status.startswith("302"))
        _, _, blocked_archive = self.client.request("GET", headers["Location"])
        self.assertIn("existe integração FTP Push ativa", blocked_archive)
        with connect() as conn:
            self.assertEqual(1, conn.execute("SELECT is_active FROM equipment WHERE id=?", (active_id,)).fetchone()[0])
        status, headers, _ = self.client.request("POST", f"/equipment/{active_id}/deactivate")
        self.assertTrue(status.startswith("302")); self.assertEqual("/equipment", headers["Location"].split("?", 1)[0])
        with connect() as conn:
            self.assertEqual(0, conn.execute("SELECT is_active FROM equipment WHERE id=?", (active_id,)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT is_active FROM mikrotik_ftp_integrations WHERE id=?", (integration_id,)).fetchone()[0])
            self.assertEqual("equipment.deactivated", conn.execute("SELECT action FROM audit_log ORDER BY id DESC").fetchone()[0])

    def test_equipment_archive_preserves_or_trashes_backups_with_admin_confirmation(self) -> None:
        self.login_ready_admin()
        preserve_id = self.create_equipment_row("archive-preserve")
        trash_id = self.create_equipment_row("archive-trash")
        self.client.multipart(f"/equipment/{preserve_id}/backups/import", {}, "preserve.cfg", b"preserve")
        self.client.multipart(f"/equipment/{trash_id}/backups/import", {}, "trash.cfg", b"trash")

        status, _, listing = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200")); self.assertIn("Arquivar equipamento", listing)
        self.assertIn('value="preserve" checked', listing); self.assertIn('value="trash"', listing)

        self.client.request("POST", f"/equipment/{preserve_id}/archive",
                            {"confirm_name": "errado", "file_action": "preserve"})
        with connect() as conn:
            self.assertEqual(1, conn.execute("SELECT is_active FROM equipment WHERE id=?", (preserve_id,)).fetchone()[0])
        self.client.request("POST", f"/equipment/{preserve_id}/archive",
                            {"confirm_name": "archive-preserve", "file_action": "preserve"})
        with connect() as conn:
            self.assertEqual(0, conn.execute("SELECT is_active FROM equipment WHERE id=?", (preserve_id,)).fetchone()[0])
            self.assertEqual("available", conn.execute("SELECT backup_status FROM backups WHERE equipment_id=?", (preserve_id,)).fetchone()[0])

        self.client.request("POST", f"/equipment/{trash_id}/archive",
                            {"confirm_name": "archive-trash", "file_action": "trash"})
        with connect() as conn:
            equipment = conn.execute("SELECT is_active FROM equipment WHERE id=?", (trash_id,)).fetchone()[0]
            backup = conn.execute("SELECT backup_status,trash_relative_path FROM backups WHERE equipment_id=?", (trash_id,)).fetchone()
            action = conn.execute("SELECT action FROM audit_log ORDER BY id DESC").fetchone()[0]
            self.assertEqual(0, equipment); self.assertEqual("trashed", backup["backup_status"])
            self.assertTrue(backup["trash_relative_path"]); self.assertEqual("equipment.archived", action)
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_orphaned_ftp_push_can_be_deactivated_without_deleting_history(self) -> None:
        self.login_ready_admin()
        equipment_id = self.create_equipment_row("delete-ftp-orphan", "mikrotik_routeros")
        with connect() as conn:
            account = create_account(conn, equipment_id=equipment_id, name="Excluída",
                                     username="deletedpush", password="DeletedPush!1")
            integration_id = conn.execute("""INSERT INTO mikrotik_ftp_integrations(uuid,equipment_id,ftp_account_id,
                routeros_major_version,backup_format,schedule_mode,schedule_frequency,schedule_time,ftp_host,ftp_port,
                ftp_directory,encrypted_ftp_secret,is_active) VALUES(?,?,?,7,'backup','manual','daily','02:00','192.0.2.1',21,'/','',1)""",
                (str(uuid.uuid4()), equipment_id, account["id"])).lastrowid
            test_id = conn.execute("""INSERT INTO mikrotik_ftp_tests(uuid,integration_id,status,expires_at)
                VALUES(?,?,'failed','2026-01-01 00:00:00')""", (str(uuid.uuid4()), integration_id)).lastrowid
            conn.execute("""INSERT INTO mikrotik_ftp_uploads(uuid,integration_id,equipment_id,test_id,original_filename,
                file_type,routeros_major_version,backup_format,status) VALUES(?,?,?,?,?,'backup',7,'backup','invalid_file')""",
                (str(uuid.uuid4()), integration_id, equipment_id, test_id, "historical.backup"))
            conn.execute("UPDATE ftp_accounts SET is_active=0,deleted_at=CURRENT_TIMESTAMP WHERE id=?", (account["id"],))

        status, _, listing = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200"))
        modal = listing.split(f'id="equipment-delete-{equipment_id}"', 1)[1].split("</dialog>", 1)[0]
        for expected in ("Conta FTP #", "<strong>excluída</strong>", "Integração FTP Push #",
                         "<strong>ativa</strong>", "vínculo órfão", "Desativar integração FTP Push"):
            self.assertIn(expected, modal)

        with mock.patch("backup_manager.app.run_helper") as helper:
            status, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration_id}/deactivate")
        self.assertTrue(status.startswith(("200", "201"))); helper.assert_not_called()
        with connect() as conn:
            self.assertEqual(0, conn.execute("SELECT is_active FROM mikrotik_ftp_integrations WHERE id=?", (integration_id,)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM mikrotik_ftp_tests WHERE integration_id=?", (integration_id,)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM mikrotik_ftp_uploads WHERE integration_id=?", (integration_id,)).fetchone()[0])

        status, _, blocked = self.client.request("POST", "/equipment/delete",
            {"id": str(equipment_id), "confirm": "1", "confirm_name": "delete-ftp-orphan"})
        self.assertTrue(status.startswith("200")); self.assertIn("dependências históricas preservadas", blocked)
        self.assertNotIn("Existe uma integração FTP Push ativa", blocked)

    def test_noc_dashboard_global_state_services_timeline_and_refresh(self) -> None:
        self.login_ready_admin()
        service_payload = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "services": {
                "backup-manager-local": {"active": "active", "sub": "running", "result": "success", "load": "loaded"},
                "worker": {"active": "inactive", "sub": "dead", "result": "success", "exit_status": "0", "load": "loaded"},
                "scheduler": {"active": "active", "sub": "waiting", "result": "success", "load": "loaded"},
                "ftp-importer": {"active": "inactive", "sub": "dead", "result": "success", "load": "loaded"},
                "pure-ftpd": {"active": "inactive", "sub": "dead", "result": "success", "load": "loaded"},
            },
        }
        with open(os.environ["BACKUP_MANAGER_SERVICE_SNAPSHOT"], "w", encoding="utf-8") as output:
            json.dump(service_payload, output)
        status, _, body = self.client.request("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Centro de Operações", body)
        self.assertIn('class="main-page-sidebar"', body)
        self.assertIn("sidebar-main-reference", body)
        self.assertIn("Todos os serviços", body)
        self.assertIn('id="nav-dashboard"', body)
        self.assertIn('href="#nav-equipment"', body)
        self.assertIn('class="brand-mark"', body)
        self.assertIn('/static/assets/backup-manager-logo.svg', body)
        self.assertNotIn('/settings/branding/logo', body)
        self.assertIn(".sidebar-main-reference .sidebar-user { display: none; }", Path("static/app.css").read_text(encoding="utf-8"))
        sidebar_css = Path("static/app.css").read_text(encoding="utf-8")
        self.assertIn("width: 100%;", sidebar_css)
        self.assertIn("font-family: inherit;", sidebar_css)
        self.assertIn("line-height: 1.25;", sidebar_css)
        self.assertIn("VERMELHO", body)
        self.assertIn("nunca executado", body)
        self.assertIn("Processamento", body)
        for section in ("Backups hoje", "Saúde dos serviços", "Precisa de atenção", "Próximas execuções",
                        "Operações em andamento", "Equipamentos por criticidade", "Resumo por ambiente"):
            self.assertIn(section, body)
        self.assertIn("window.location.reload(), 30000", body)
        self.assertNotIn("alert(", body)

        status, _, backups_body = self.client.request("GET", "/backups")
        self.assertTrue(status.startswith("200"))
        self.assertIn('class="main-page-sidebar"', backups_body)
        self.assertIn("sidebar-main-reference", backups_body)
        self.assertIn('href="#nav-backups"', backups_body)
        self.assertIn('/static/assets/backup-manager-logo.svg', backups_body)
        self.assertNotIn('/settings/branding/logo', backups_body)
        self.assertIn('<span class="nav-group-title">Serviços</span>', backups_body)
        self.assertIn('href="/ftp"', backups_body)
        self.assertIn('href="/cloud"', backups_body)
        self.assertIn('class="sidebar-system-status"', backups_body)

        self.client.multipart("/equipment/1/backups/import", {}, "noc.cfg", b"hostname noc")
        with connect() as conn:
            conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                         VALUES(1,'ssh.backup_failed','equipment','1','{}','')""")
            snapshot = dashboard_snapshot(conn)
        equipment = next(item for item in snapshot["equipment"] if item["id"] == 1)
        self.assertEqual(equipment["level"], "green")
        self.assertEqual(snapshot["level"], "yellow")  # health-check ainda nao executado
        self.assertEqual(snapshot["timeline"][0]["label"], "Erro SSH")
        self.assertEqual(snapshot["timeline"][0]["level"], "red")
        with connect() as conn:
            job_uuid, run_uuid = str(uuid.uuid4()), str(uuid.uuid4())
            job_id = conn.execute("""INSERT INTO backup_jobs(uuid,equipment_id,method,status)
                                  VALUES(?,1,'ssh','active')""", (job_uuid,)).lastrowid
            conn.execute("""INSERT INTO backup_job_runs(uuid,job_id,equipment_id,method,status,error_code)
                         VALUES(?,?,1,'ssh','failed','SSH_CONNECTION_FAILED')""", (run_uuid, job_id))
            critical = dashboard_snapshot(conn)
        self.assertEqual(critical["level"], "red")
        self.assertTrue(any(item["source"] == "SSH" for item in critical["alerts"]))
        self.assertTrue(any(item["source"] == "Jobs" for item in critical["alerts"]))
        with connect() as conn:
            conn.execute("""INSERT INTO backup_job_runs(uuid,job_id,equipment_id,method,status)
                         VALUES(?,?,1,'ssh','success')""", (str(uuid.uuid4()), job_id))
            resolved = dashboard_snapshot(conn)
        self.assertEqual(resolved["jobs"]["active_failed"], 0)
        self.assertFalse(any(item["source"] in {"SSH", "Jobs"} for item in resolved["alerts"]))
        self.assertEqual(resolved["jobs"]["failed_24h"], 1)  # histórico preservado

    def test_dashboard_operational_summary_coverage_schedule_rate_and_empty_states(self) -> None:
        self.login_ready_admin()
        self.assertEqual(quantity(1, "alerta"), "1 alerta")
        self.assertEqual(quantity(2, "alerta"), "2 alertas")
        self.assertEqual(short_identifier("1234567890abcdef", 8), "12345678…")
        self.assertEqual(relative_time("2026-07-12 11:00:00", datetime(2026, 7, 12, 12, tzinfo=timezone.utc)), "há 1 h")
        self.assertEqual(backup_status(None, datetime.now(timezone.utc))["status"], "nunca executado")

        with connect() as conn:
            empty = dashboard_snapshot(conn)
        self.assertEqual(empty["coverage"]["percent"], 0)
        self.assertEqual(empty["coverage"]["unprotected"], 1)
        self.assertIsNone(empty["last_backup"])
        self.assertIsNone(empty["next_backup"])
        self.assertIsNone(empty["success_rate"]["percent"])
        status, _, body = self.client.request("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Novo equipamento", body)
        self.assertIn("Backups hoje", body)
        self.assertIn("Nenhuma execução agendada", body)
        self.assertIn("Equipamentos protegidos", body)
        self.assertIn("Taxa de sucesso", body)

        self.client.multipart("/equipment/1/backups/import", {}, "summary.cfg", b"hostname summary")
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        with connect() as conn:
            job_uuid = str(uuid.uuid4())
            job_id = conn.execute("""INSERT INTO backup_jobs(uuid,equipment_id,job_type,method,schedule_enabled,
                schedule_type,schedule_time,timezone,status,next_run_at) VALUES(?,1,'scheduled','dry_run',1,
                'daily','23:00','America/Sao_Paulo','active',?)""", (job_uuid, future)).lastrowid
            for status_name in ("success", "success", "failed"):
                conn.execute("""INSERT INTO backup_job_runs(uuid,job_id,equipment_id,method,status)
                    VALUES(?,?,1,'dry_run',?)""", (str(uuid.uuid4()), job_id, status_name))
            for index in range(8):
                conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                    VALUES(1,'backup.created_from_ssh','backup',?,'{}','')""", (f"identifier-{index}-long-value",))
            summary = dashboard_snapshot(conn)
        self.assertEqual(summary["coverage"]["percent"], 100)
        self.assertEqual(summary["coverage"]["unprotected"], 0)
        self.assertIsNotNone(summary["last_backup"])
        self.assertEqual(summary["next_backup"]["uuid"], job_uuid)
        self.assertEqual(summary["upcoming_backups"][0]["uuid"], job_uuid)
        self.assertEqual(summary["success_rate"]["percent"], 66.7)
        self.assertEqual(len(summary["timeline"]), 9)  # limite é aplicado na apresentação
        self.assertEqual(summary["environments"][0]["equipment"], 1)
        status, _, body = self.client.request("GET", "/")
        self.assertIn("100%", body)
        self.assertIn("66,7%", body)
        self.assertIn("Verificação", body)
        self.assertIn("Atividade recente", body)
        self.assertNotIn("identifier-7", body)

    def test_dashboard_zero_equipment_and_viewer_quick_actions(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET is_active=0")
            snapshot = dashboard_snapshot(conn)
        self.assertEqual(snapshot["coverage"]["total"], 0)
        self.assertIsNone(snapshot["coverage"]["percent"])
        status, _, body = self.client.request("GET", "/")
        self.assertIn("Nenhum equipamento ativo", body)
        self.assertIn("0 <em>/ 0</em>", body)
        with connect() as conn:
            conn.execute(
                "INSERT INTO users(username,full_name,password_hash,role_id,must_change_password) "
                "VALUES(?,?,?,(SELECT id FROM roles WHERE slug='read_download'),0)",
                ("dashboardviewer", "Dashboard Viewer", hash_password("ViewerSenha!2026")),
            )
        viewer = WsgiClient()
        viewer.request("POST", "/login", {"username": "dashboardviewer", "password": "ViewerSenha!2026"})
        status, _, viewer_body = viewer.request("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Ver falhas", viewer_body)
        self.assertNotIn("Novo equipamento", viewer_body)
        self.assertNotIn("Executar verificação de integridade", viewer_body)

    def test_notification_settings_hide_token_and_test_is_asynchronous(self) -> None:
        self.login_ready_admin()
        token = "1234567:" + "abcdefghijklmnopqrstuvwxyzABCDE"  # credencial sintética
        status, headers, _ = self.client.request("POST", "/settings/notifications", {
            "is_enabled": "1", "token": token, "chat_id": "-1001234567890", "cooldown_minutes": "60",
            "timezone": "America/Sao_Paulo", "maintenance_start": "23:00", "maintenance_end": "01:00",
            "daily_enabled": "1", "daily_time": "08:00", "weekly_enabled": "1", "weekly_day": "0", "weekly_time": "08:00",
            "csrf_token": self.settings_csrf(),
        })
        self.assertTrue(status.startswith("302"))
        self.assertIn("notice=success", headers["Location"])
        status, _, body = self.client.request("GET", "/settings/notifications")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Integração Telegram", body)
        self.assertIn("configurado", body)
        self.assertIn("data-secret-toggle", body)
        self.assertNotIn(token, body)
        with connect() as conn:
            channel = conn.execute("SELECT * FROM notification_channels WHERE channel_type='telegram'").fetchone()
            self.assertNotEqual(channel["token_encrypted"], token)

        with mock.patch("backup_manager.notifications.TelegramTransport.send") as send:
            session = self.client.cookie.split("=", 1)[1]
            csrf = hashlib.sha256(("session-csrf:" + session).encode()).hexdigest()
            status, headers, _ = self.client.request("POST", "/settings/notifications/test", {"csrf_token": csrf})
            self.assertTrue(status.startswith("302"))
            self.assertIn("notice=info", headers["Location"])
            send.assert_not_called()
        with connect() as conn:
            queued = conn.execute("SELECT * FROM notification_queue WHERE event_type='telegram.basic_test'").fetchone()
            self.assertEqual(queued["status"], "pending")
            self.assertIsNotNone(queued["destination_id"])
            self.assertIsNone(queued["thread_id"])
            self.assertIn("Integração Telegram funcionando", queued["message"])
            self.assertNotIn("-1001234567890", queued["message"])

            class SuccessTransport:
                def send(self, supplied_token, chat_id, message, thread_id=None):
                    self.assert_token = supplied_token
                    self.message = message
                    self.thread_id = thread_id
                    return 200, "42"

            transport = SuccessTransport()
            sent, retried, failed = notification_dispatch(conn, transport=transport)
            self.assertEqual((sent, retried, failed), (1, 0, 0))
            self.assertEqual(transport.assert_token, token)
            self.assertIn("Integração Telegram funcionando", transport.message)
            self.assertIsNone(transport.thread_id)
            history = conn.execute("SELECT status FROM notification_history ORDER BY id DESC LIMIT 1").fetchone()[0]
            self.assertEqual(history, "sent")

        status, headers, _ = self.client.request("POST", "/settings/notifications/summary-test", {"csrf_token": csrf, "kind": "daily"})
        self.assertTrue(status.startswith("302")); self.assertIn("notice=info", headers["Location"])
        with connect() as conn:
            logical = conn.execute("SELECT logical_key FROM telegram_summary_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
            self.assertTrue(logical.startswith("test:daily:"))

    def test_notification_basic_test_uses_default_supergroup_topic(self) -> None:
        self.login_ready_admin()
        token = "1234567:" + "abcdefghijklmnopqrstuvwxyzABCDE"
        status, _, _ = self.client.request("POST", "/settings/notifications", {
            "is_enabled": "1", "token": token, "chat_id": "-1001234567890", "cooldown_minutes": "60",
            "timezone": "America/Sao_Paulo", "maintenance_start": "23:00", "maintenance_end": "01:00",
            "daily_enabled": "1", "daily_time": "08:00", "weekly_enabled": "1", "weekly_day": "0", "weekly_time": "08:00",
            "csrf_token": self.settings_csrf(),
        })
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            destination = conn.execute("SELECT id FROM telegram_destinations WHERE chat_id='-1001234567890' ORDER BY id LIMIT 1").fetchone()
            conn.execute("UPDATE telegram_destinations SET default_thread_id=1411,use_for_alerts=1 WHERE id=?", (destination["id"],))
            from backup_manager.notifications import queue_basic_test
            queue_uuid = queue_basic_test(conn)
            queued = conn.execute("SELECT destination_id,thread_id FROM notification_queue WHERE uuid=?", (queue_uuid,)).fetchone()
            self.assertEqual(destination["id"], queued["destination_id"])
            self.assertEqual(1411, queued["thread_id"])

    def test_notification_settings_enforce_csrf_and_admin_rbac(self) -> None:
        self.login_ready_admin()
        status, _, body = self.client.request("POST", "/settings/notifications", {
            "is_enabled": "1", "chat_id": "-1001234567890", "cooldown_minutes": "60",
            "timezone": "America/Sao_Paulo", "daily_time": "08:00", "weekly_day": "0", "weekly_time": "08:00",
        })
        self.assertTrue(status.startswith("403"))
        self.assertIn("Token CSRF inválido", body)
        viewer = WsgiClient()
        with connect() as conn:
            conn.execute("INSERT INTO users(username,full_name,password_hash,role_id,must_change_password) VALUES(?,?,?,(SELECT id FROM roles WHERE slug='read_download'),0)",
                         ("notificationviewer", "Notification Viewer", hash_password("ViewerSenha!2026")))
        viewer.request("POST", "/login", {"username": "notificationviewer", "password": "ViewerSenha!2026"})
        status, _, body = viewer.request("GET", "/settings/notifications")
        self.assertTrue(status.startswith("403"))
        self.assertNotIn("Token do bot", body)

    def test_telegram_token_change_invalidates_diagnostic(self) -> None:
        self.login_ready_admin()
        from backup_manager.notifications import telegram_config_hash
        old = "1234567:abcdefghijklmnopqrstuvwxyzABCDE"
        new = "7654321:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde"
        self.save_telegram(old, "-1001234567890")
        with connect() as conn:
            for key, value in {"telegram_diag_bot": "@old_bot", "telegram_diag_chat": "OLD",
                               "telegram_diag_config_hash": telegram_config_hash(old, "-1001234567890")}.items():
                conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, value))
        self.save_telegram(new, "-1001234567890")
        with connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'telegram_diag_%'").fetchone()[0])

    def test_telegram_chat_id_change_invalidates_diagnostic(self) -> None:
        self.login_ready_admin()
        token = "1234567:abcdefghijklmnopqrstuvwxyzABCDE"
        self.save_telegram(token, "-1001234567890")
        with connect() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('telegram_diag_bot','@old_bot')")
        self.save_telegram("", "-1009876543210")
        with connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'telegram_diag_%'").fetchone()[0])

    def test_telegram_token_and_chat_change_never_revives_old_diagnostic(self) -> None:
        self.login_ready_admin()
        from backup_manager.notifications import telegram_config_hash
        old = "1234567:abcdefghijklmnopqrstuvwxyzABCDE"
        new = "7654321:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde"
        self.save_telegram(old, "-1001234567890")
        with connect() as conn:
            values = {"telegram_diag_bot": "@old_bot", "telegram_diag_chat": "OLD DESTINATION",
                      "telegram_diag_chat_type": "supergroup", "telegram_diag_can_send": "1",
                      "telegram_diag_config_hash": telegram_config_hash(old, "-1001234567890")}
            for key, value in values.items(): conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, value))
        self.save_telegram(new, "-1009876543210")
        status, _, body = self.client.request("GET", "/settings/notifications")
        self.assertTrue(status.startswith("200")); self.assertNotIn("old_bot", body); self.assertNotIn("OLD DESTINATION", body)
        self.assertIn("Não verificado", body); self.assertIn("Não verificada", body)

    def test_telegram_current_diagnostic_updates_interface_without_exposing_secret(self) -> None:
        self.login_ready_admin()
        from backup_manager.cli import telegram_diagnose
        token = "1234567:abcdefghijklmnopqrstuvwxyzABCDE"
        self.save_telegram(token, "-1001234567890")

        class DiagnosticTransport:
            def diagnose(self, supplied_token, chat_id, send_test=False):
                self.supplied = (supplied_token, chat_id, send_test)
                return {"bot_id": 7, "username": "Hconecta_bot", "chat_title": "ALERTAS_HER_CONECTA",
                        "chat_type": "supergroup", "is_forum": False, "member_status": "administrator",
                        "can_send": True, "sent_message_id": ""}

        transport = DiagnosticTransport()
        with mock.patch("builtins.print") as printed:
            self.assertEqual(0, telegram_diagnose(transport=transport))
        diagnostic_output = " ".join(str(call) for call in printed.call_args_list)
        self.assertNotIn(token, diagnostic_output)
        self.assertEqual((token, "-1001234567890", False), transport.supplied)
        status, _, body = self.client.request("GET", "/settings/notifications")
        self.assertTrue(status.startswith("200")); self.assertIn("@Hconecta_bot", body)
        self.assertIn("ALERTAS_HER_CONECTA", body); self.assertIn("Pode enviar", body)
        self.assertIn("Última verificação", body); self.assertNotIn(token, body)
        with connect() as conn:
            stored = " ".join(row[0] for row in conn.execute("SELECT value FROM settings WHERE key LIKE 'telegram_diag_%'"))
            audits = " ".join(row[0] for row in conn.execute("SELECT details FROM audit_log"))
        self.assertNotIn(token, stored); self.assertNotIn(token, audits)

    def test_notification_test_operational_error_logs_audits_and_redirects(self) -> None:
        self.login_ready_admin()
        session = self.client.cookie.split("=", 1)[1]
        csrf = hashlib.sha256(("session-csrf:" + session).encode()).hexdigest()
        with mock.patch("backup_manager.app.queue_basic_test", side_effect=sqlite3.IntegrityError("synthetic failure")), \
             self.assertLogs("backup_manager.web", level="ERROR") as logs:
            status, headers, body = self.client.request("POST", "/settings/notifications/test", {"csrf_token": csrf})
        self.assertTrue(status.startswith("302")); self.assertEqual("", body)
        self.assertIn("/settings?tab=notifications&notice=error", headers["Location"])
        self.assertIn("Traceback", "\n".join(logs.output))
        with connect() as conn:
            event = conn.execute("SELECT action,details FROM audit_log WHERE action='telegram.basic_test_failed' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(event); self.assertIn("IntegrityError", event["details"])

    def test_notification_test_global_failure_also_redirects_and_logs(self) -> None:
        with mock.patch("backup_manager.app.require_schema_current", side_effect=RuntimeError("startup request failure")), \
             self.assertLogs("backup_manager.web", level="ERROR") as logs:
            status, headers, body = self.client.request("POST", "/settings/notifications/test")
        self.assertTrue(status.startswith("302")); self.assertEqual("", body)
        self.assertIn("/settings?tab=notifications&notice=error", headers["Location"])
        self.assertIn("Traceback", "\n".join(logs.output))

    def test_notification_cooldown_recovery_maintenance_and_retry(self) -> None:
        self.login_ready_admin()
        from backup_manager.notifications import configure_telegram, enqueue, scan_audit_events, schedule_summaries
        base = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
        with connect() as conn:
            configure_telegram(conn, enabled=True, token="1234567:" + "abcdefghijklmnopqrstuvwxyzABCDE", chat_id="-1001234567890",
                               cooldown_minutes=60, maintenance_enabled=False, maintenance_start="00:00", maintenance_end="00:00",
                               timezone_name="UTC", daily_enabled=False, daily_time="08:00", weekly_enabled=False,
                               weekly_day=0, weekly_time="08:00", user_id=1)
            self.assertTrue(record_condition(conn, key="service:worker", active=True, event_type="worker_offline",
                                             severity="critical", subject="Worker offline", message="offline",
                                             recovery_message="online", now=base))
            self.assertFalse(record_condition(conn, key="service:worker", active=True, event_type="worker_offline",
                                              severity="critical", subject="Worker offline", message="offline",
                                              recovery_message="online", now=base + timedelta(minutes=30)))
            self.assertTrue(record_condition(conn, key="service:worker", active=True, event_type="worker_offline",
                                             severity="critical", subject="Worker offline", message="offline",
                                             recovery_message="online", now=base + timedelta(minutes=61)))
            self.assertTrue(record_condition(conn, key="service:worker", active=False, event_type="worker_offline",
                                             severity="critical", subject="Worker offline", message="offline",
                                             recovery_message="online", now=base + timedelta(minutes=62)))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notification_queue WHERE dedup_key='service:worker'").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notification_queue WHERE severity='recovery'").fetchone()[0], 1)

            conn.execute("UPDATE notification_channels SET maintenance_enabled=1,maintenance_start='00:00',maintenance_end='00:00'")
            enqueue(conn, event_type="ftp_rejected", severity="warning", subject="Rejeitado", message="arquivo",
                    dedup_key="maintenance:test", now=base)
            self.assertEqual(conn.execute("SELECT status FROM notification_queue WHERE dedup_key='maintenance:test'").fetchone()[0], "suppressed")
            conn.execute("UPDATE notification_channels SET maintenance_enabled=0")
            conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                         VALUES(NULL,'backup.created_from_ssh','backup','new-backup','{}','')""")
            scanned, queued = scan_audit_events(conn, base)
            self.assertGreaterEqual(scanned, 1)
            self.assertEqual(queued, 1)

            class FailureTransport:
                def send(self, token, chat_id, message):
                    raise RuntimeError("network secret must not leak")

            sent, retried, failed = notification_dispatch(conn, transport=FailureTransport(), now=base)
            self.assertEqual(sent, 0)
            self.assertGreater(retried, 0)
            self.assertEqual(failed, 0)
            retry = conn.execute("SELECT * FROM notification_history WHERE status='retry' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(retry["error_code"], "telegram_delivery_failed")
            conn.execute("""UPDATE notification_channels SET daily_summary_enabled=1,daily_summary_time='08:00',
                         weekly_summary_enabled=1,weekly_summary_day=5,weekly_summary_time='08:00'""")
            self.assertEqual(schedule_summaries(conn, base), 2)
            self.assertEqual(schedule_summaries(conn, base), 0)
            audit_text = " ".join(row[0] for row in conn.execute("SELECT details FROM audit_log"))
            self.assertNotIn("1234567:", audit_text)

    def test_operational_telegram_messages_are_friendly_private_and_idempotent(self) -> None:
        self.login_ready_admin()
        from backup_manager.notifications import scan_audit_events
        base = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)
        with connect() as conn:
            conn.execute("UPDATE notification_channels SET timezone='America/Sao_Paulo',is_enabled=0")
            conn.execute("UPDATE notification_cursors SET last_id=(SELECT COALESCE(MAX(id),0) FROM audit_log) WHERE source='audit_log'")
            group_id = conn.execute("INSERT INTO equipment_groups(name) VALUES('OLT')").lastrowid
            equipment_id = conn.execute("INSERT INTO equipment(hostname,ip_address,name,group_id) VALUES('OLT & Centro','192.0.2.10','OLT Centro',?)", (group_id,)).lastrowid
            backup_uuid = str(uuid.uuid4())
            backup_id = conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,
                source_method,backup_status,received_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (backup_uuid, equipment_id, "olt-<centro>.cfg", "safe.cfg", "backups/internal/safe.cfg", 1024, "a" * 64,
                 "ftp", "available", "2026-07-20 06:00:00", "token=never-show password=secret")).lastrowid
            technical = {"equipment_id": equipment_id, "backup_id": backup_id, "backup_uuid": backup_uuid,
                         "filename": "olt-<centro>.cfg", "path": "/internal/secret", "token": "never-show"}
            for _ in range(2):
                conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address,created_at)
                    VALUES(NULL,'backup.created_from_ftp','ftp_received_file',?,?, '',?)""",
                    (str(uuid.uuid4()), json.dumps(technical), "2026-07-20 06:00:00"))
            scanned, queued = scan_audit_events(conn, base)
            self.assertEqual((2, 1), (scanned, queued))
            item = conn.execute("SELECT * FROM notification_queue WHERE event_type='ftp_received_file'").fetchone()
            visible = f"{item['subject']}\n{item['message']}"
            self.assertIn("Backup recebido por FTP", visible)
            self.assertIn("OLT & Centro", visible)
            self.assertIn("Arquivo: Backup de configuração", visible)
            self.assertNotIn("olt-<centro>.cfg", visible)
            self.assertIn("Grupo: OLT", visible)
            self.assertNotIn(backup_uuid, visible)
            for forbidden in ("Entidade", "Identificador", "ftp_received_file", "lifecycle", "/internal", "never-show", "password"):
                self.assertNotIn(forbidden, visible)
            self.assertIn(backup_uuid, " ".join(row[0] for row in conn.execute("SELECT details FROM audit_log WHERE action='backup.created_from_ftp'")))

            # Rewind simulates a worker restart/reprocessing the same audit rows.
            conn.execute("UPDATE notification_cursors SET last_id=0 WHERE source='audit_log'")
            scan_audit_events(conn, base)
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM notification_queue WHERE event_type='ftp_received_file'").fetchone()[0])

            second_uuid = str(uuid.uuid4())
            conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,
                source_method,backup_status,received_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (second_uuid, equipment_id, "outro.cfg", "outro.cfg", "backups/outro.cfg", 10, "b" * 64, "ftp", "available", "2026-07-20 06:01:00"))
            conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address,created_at)
                VALUES(NULL,'backup.created_from_ftp','ftp_received_file','account',?,'','2026-07-20 06:01:00')""",
                (json.dumps({"backup_uuid": second_uuid, "filename": "outro.cfg"}),))
            scan_audit_events(conn, base)
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM notification_queue WHERE event_type='ftp_received_file'").fetchone()[0])

    def test_operational_telegram_unidentified_ftp_and_cleanup_templates(self) -> None:
        self.login_ready_admin()
        from backup_manager.notifications import scan_audit_events
        base = datetime(2026, 7, 20, 6, 38, tzinfo=timezone.utc)
        with connect() as conn:
            conn.execute("UPDATE notification_channels SET timezone='America/Sao_Paulo',is_enabled=0")
            conn.execute("UPDATE notification_cursors SET last_id=(SELECT COALESCE(MAX(id),0) FROM audit_log) WHERE source='audit_log'")
            events = (
                ("backup.created_from_ftp", "ftp_received_file", "account", {"filename": "backup-20260720.cfg"}),
                ("lifecycle.trash_emptied", "lifecycle", "trash", {"removed": 12, "bytes": 84 * 1024 * 1024}),
                ("lifecycle.executed", "lifecycle", str(uuid.uuid4()), {"purged": 8, "purged_bytes": 210 * 1024 * 1024}),
                ("lifecycle.executed", "lifecycle", str(uuid.uuid4()), {"purged": 0, "purged_bytes": 0}),
            )
            for action, entity, entity_id, details in events:
                conn.execute("INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address,created_at) VALUES(NULL,?,?,?,?, '',?)",
                             (action, entity, entity_id, json.dumps(details), "2026-07-20 06:38:00"))
            scanned, queued = scan_audit_events(conn, base)
            self.assertEqual((4, 3), (scanned, queued))
            messages = {row["event_type"]: f"{row['subject']}\n{row['message']}" for row in conn.execute(
                "SELECT event_type,subject,message FROM notification_queue WHERE event_type IN ('ftp_received_file','lifecycle_trash_cleanup','lifecycle_retention_cleanup')")}
            self.assertIn("Backup recebido sem identificação", messages["ftp_received_file"])
            self.assertIn("backup-20260720.cfg", messages["ftp_received_file"])
            self.assertIn("Itens removidos: 12", messages["lifecycle_trash_cleanup"])
            self.assertIn("Espaço liberado: 84 MB", messages["lifecycle_trash_cleanup"])
            self.assertIn("Limpeza de backups expirados concluída", messages["lifecycle_retention_cleanup"])
            self.assertIn("Arquivos removidos: 8", messages["lifecycle_retention_cleanup"])
            self.assertIn("Espaço liberado: 210 MB", messages["lifecycle_retention_cleanup"])
            self.assertNotIn("lifecycle", "\n".join(messages.values()).lower())

    def test_operational_ssh_notification_shows_equipment_file_and_success_icon(self) -> None:
        self.login_ready_admin()
        from backup_manager.notifications import dispatch as notification_dispatch, scan_audit_events
        base = datetime(2026, 7, 20, 15, 4, tzinfo=timezone.utc)
        self.save_telegram("1234567:abcdefghijklmnopqrstuvwxyzABCDE", "-1001234567890")

        class CaptureTransport:
            message = ""

            def send(self, token, chat_id, message, *args):
                self.message = message
                return 200, "1"

        with connect() as conn:
            conn.execute("UPDATE notification_channels SET timezone='America/Sao_Paulo',is_enabled=1")
            conn.execute("UPDATE notification_cursors SET last_id=(SELECT COALESCE(MAX(id),0) FROM audit_log) WHERE source='audit_log'")
            conn.execute("UPDATE equipment SET name='CE-BASE-CONECTA',hostname='router-interno' WHERE id=1")
            backup_uuid = str(uuid.uuid4())
            filename = "ce-base-conecta_2026-07-20_12-04.rsc"
            conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,
                source_method,backup_status,received_at) VALUES(?,1,?,?,?,1024,?,'ssh','available','2026-07-20 15:04:00')""",
                (backup_uuid, filename, "safe.rsc", "backups/internal/safe.rsc", "a" * 64))
            conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address,created_at)
                VALUES(NULL,'backup.created_from_ssh','backup',?,?, '',?)""",
                (backup_uuid, json.dumps({"equipment_id": 1, "driver": "mikrotik_routeros", "size": 1024}), "2026-07-20 15:04:00"))
            self.assertEqual(scan_audit_events(conn, base), (1, 1))
            item = conn.execute("SELECT subject,message FROM notification_queue WHERE event_type='backup_completed' ORDER BY id DESC LIMIT 1").fetchone()
            visible = f"{item['subject']}\n{item['message']}"
            self.assertIn("Equipamento: CE-BASE-CONECTA", visible)
            self.assertIn("Arquivo: Backup de configuração", visible)
            self.assertNotIn(filename, visible)
            self.assertNotIn(backup_uuid, visible)
            transport = CaptureTransport()
            self.assertEqual(notification_dispatch(conn, transport=transport, now=base)[0], 1)
            self.assertIn("✅ <b>Backup SSH concluído</b>", transport.message)
            self.assertIn("CE-BASE-CONECTA", transport.message)
            self.assertIn("Backup de configuração", transport.message)
            self.assertNotIn(filename, transport.message)
            self.assertNotIn(backup_uuid, transport.message)

    def test_equipment_operational_notifications_use_names_dates_and_html_escape_on_delivery(self) -> None:
        self.login_ready_admin()
        from backup_manager.notifications import dispatch, evaluate_conditions
        base = datetime(2026, 7, 20, 6, 36, tzinfo=timezone.utc)
        snapshot = {"equipment": [
            {"id": 901, "hostname": "SEM <BACKUP>", "last_backup": None, "age_hours": None},
            {"id": 902, "hostname": "ATRASADO & POP", "last_backup": "2026-07-18 05:15:00", "age_hours": 49.35},
        ], "services": [], "storage": {"percent": 0}}
        with connect() as conn, mock.patch("backup_manager.notifications.dashboard_snapshot", return_value=snapshot):
            conn.execute("UPDATE notification_channels SET timezone='America/Sao_Paulo',is_enabled=0")
            self.assertEqual(2, evaluate_conditions(conn, base))
            missing = conn.execute("SELECT message FROM notification_queue WHERE event_type='equipment_without_backup'").fetchone()[0]
            late = conn.execute("SELECT message FROM notification_queue WHERE event_type='backup_late'").fetchone()[0]
            self.assertIn("Equipamento: SEM <BACKUP>", missing)
            self.assertIn("Última verificação: 20/07/2026 03:36", missing)
            self.assertIn("Equipamento: ATRASADO & POP", late)
            self.assertIn("Último backup: 18/07/2026 02:15", late)

            conn.execute("UPDATE notification_channels SET is_enabled=1,token_encrypted=?,chat_id='-1001234567890'", (encrypt_secret("1234567:" + "abcdefghijklmnopqrstuvwxyzABCDE"),))
            conn.execute("UPDATE notification_queue SET status='pending',next_attempt_at='2026-07-20 06:36:00'")
            delivered = []
            class CaptureTransport:
                def send(self, token, chat_id, message, *args):
                    delivered.append(message); return 200, "1"
            dispatch(conn, transport=CaptureTransport(), now=base)
            rendered = "\n".join(delivered)
            self.assertIn("SEM &lt;BACKUP&gt;", rendered)
            self.assertIn("ATRASADO &amp; POP", rendered)
            self.assertNotIn("SEM <BACKUP>", rendered)

    def test_rc1_operational_checks_and_friendly_internal_error(self) -> None:
        self.login_ready_admin()
        self.assertEqual(jobs_check(), 0)
        self.assertEqual(notification_check(), 0)
        with mock.patch("backup_manager.app.dashboard_snapshot", side_effect=sqlite3.OperationalError("/internal/path database locked")):
            status, _, body = self.client.request("GET", "/")
        self.assertTrue(status.startswith("500"))
        self.assertIn("Não foi possível concluir a operação", body)
        self.assertNotIn("database locked", body)
        self.assertNotIn("/internal/path", body)


    def login_ready_admin(self) -> None:
        self.client.request("POST", "/login", {"username": "admin", "password": TEMP_ADMIN_PASSWORD})
        self.client.request(
            "POST",
            "/change-password",
            {
                "current_password": TEMP_ADMIN_PASSWORD,
                "new_password": "NovaSenha!2026",
                "confirm_password": "NovaSenha!2026",
            },
        )
        self.client.request(
            "POST",
            "/setup",
            {
                "installation_name": "Backup Manager Lab",
                "provider_name": "Provedor Local",
                "timezone": "America/Sao_Paulo",
                "storage_root": os.environ["BACKUP_MANAGER_STORAGE_ROOT"],
                "primary_environment": "Producao",
                "pops": "POP Principal, Sao Paulo, SP",
            },
        )
        self.client.request(
            "POST",
            "/equipment",
            {
                "hostname": "router-edge-01",
                "ip_address": "10.0.0.2",
                "vendor_id": "1",
                "group_id": "1",
                "pop_id": "1",
                "environment_id": "1",
                "notes": "",
            },
        )

    def test_manual_backup_flow_permissions_and_trash(self) -> None:
        self.login_ready_admin()
        status, _, body = self.client.multipart("/equipment/1/backups/import", {"notes": "manual"}, "router.cfg", b"hostname router\n")
        self.assertTrue(status.startswith("302"), body.decode("utf-8", errors="ignore"))

        status, _, body = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("router.cfg", body)
        self.assertIn("Sucesso", body)

        status, _, body = self.client.request("GET", "/backups?name=router")
        self.assertTrue(status.startswith("200"))
        self.assertIn("router-edge-01", body)

        with connect() as conn:
            row = conn.execute("SELECT uuid, sha256, download_count FROM backups").fetchone()
        status, _, detail = self.client.request("GET", f"/backups/{row['uuid']}")
        self.assertTrue(status.startswith("200"))
        for expected in ("Detalhes do backup", "Disponível", "Voltar ao histórico", "Informações do arquivo", "Sincronização externa", "Telegram"):
            self.assertIn(expected, detail)
        self.assertEqual(row["sha256"], "dbef80de5fdb05e8d67e0a8f387c74a557ff8e0c863151391eb8e7fc4f595bbb")
        status, headers, body = self.client.request("GET", f"/backups/{row['uuid']}/download")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertIn("hostname router", body)

        with connect() as conn:
            count = conn.execute("SELECT download_count FROM backups WHERE uuid = ?", (row["uuid"],)).fetchone()[0]
        self.assertEqual(count, 1)

        anonymous = WsgiClient()
        status, _, _ = anonymous.request("GET", f"/backups/{row['uuid']}/download")
        self.assertTrue(status.startswith("302"))
        status, _, _ = self.client.request("GET", "/backups/../../etc/passwd/download")
        self.assertTrue(status.startswith("404"))

        status, _, body = self.client.multipart("/equipment/1/backups/import", {}, "empty.cfg", b"")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Arquivo vazio", body.decode())
        status, _, body = self.client.multipart("/equipment/1/backups/import", {}, "bad.exe", b"x")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Extensao nao permitida", body.decode())
        with connect() as conn:
            conn.execute("UPDATE settings SET value = '10' WHERE key = 'maximum_upload_size'")
        status, _, body = self.client.multipart("/equipment/1/backups/import", {}, "large.cfg", b"01234567890")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Arquivo acima do limite", body.decode())
        with connect() as conn:
            conn.execute("UPDATE settings SET value = ? WHERE key = 'maximum_upload_size'", (str(50 * 1024 * 1024),))

        status, _, _ = self.client.request("GET", "/static/router.cfg")
        self.assertTrue(status.startswith("404"))

        with connect() as conn:
            conn.execute(
                "INSERT INTO users(username, full_name, password_hash, role_id, must_change_password) VALUES (?, ?, ?, (SELECT id FROM roles WHERE slug = 'read_download'), 0)",
                ("viewer", "Viewer", hash_password("ViewerSenha!2026")),
            )
        viewer = WsgiClient()
        viewer.request("POST", "/login", {"username": "viewer", "password": "ViewerSenha!2026"})
        status, _, _ = viewer.multipart("/equipment/1/backups/import", {}, "viewer.cfg", b"x")
        self.assertTrue(status.startswith("403"))
        status, _, body = viewer.request("GET", f"/backups/{row['uuid']}/download")
        self.assertTrue(status.startswith("200"))
        self.assertIn("hostname router", body)
        status, _, _ = viewer.request("POST", f"/backups/{row['uuid']}/trash")
        self.assertTrue(status.startswith("403"))

        status, headers, _ = self.client.request("POST", f"/backups/{row['uuid']}/trash")
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            trashed = conn.execute("SELECT backup_status, trash_relative_path FROM backups WHERE uuid = ?", (row["uuid"],)).fetchone()
        self.assertEqual(trashed["backup_status"], "trashed")
        self.assertTrue(trashed["trash_relative_path"])

        status, _, _ = self.client.request("POST", f"/backups/{row['uuid']}/restore")
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            restored = conn.execute("SELECT backup_status FROM backups WHERE uuid = ?", (row["uuid"],)).fetchone()[0]
        self.assertEqual(restored, "available")

        self.client.request("POST", f"/backups/{row['uuid']}/trash")
        status, _, _ = self.client.request("POST", f"/backups/{row['uuid']}/delete")
        self.assertTrue(status.startswith("404"))
        with connect() as conn:
            deleted = conn.execute("SELECT backup_status, relative_path FROM backups WHERE uuid = ?", (row["uuid"],)).fetchone()
        self.assertEqual(deleted["backup_status"], "trashed")
        self.assertIsNotNone(deleted["relative_path"])

    def test_mikrotik_ftp_push_modal_creation_test_rotation_and_deactivation(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik'),name='MK Core' WHERE id=1")
            conn.execute("UPDATE settings SET value='192.0.2.10' WHERE key='ftp_public_ip'")
        status, _, body = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn('id="mikrotik-ftp-config"', body)
        self.assertIn("Não configurado", body)
        self.assertIn("Configurar FTP Push", body)
        self.assertIn('name="ftp_directory" value="/"', body)
        self.assertIn("Conta FTP", body)
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, headers, body = self.client.request("POST", "/equipment/1/mikrotik-ftp", {
                "routeros_version": "6", "backup_format": "backup", "schedule_mode": "manual",
                "schedule_frequency": "daily", "schedule_time": "02:15", "ftp_host": "192.0.2.10",
                "ftp_port": "21", "ftp_directory": "/routeros/backups", "retention_days": "30",
            })
        self.assertTrue(status.startswith("302"), body)
        self.assertIn("/equipment/1?", headers["Location"])
        self.assertIn("Instala%C3%A7%C3%A3o+guiada", headers["Location"])
        with connect() as conn:
            integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations").fetchone()
            account = conn.execute("SELECT * FROM ftp_accounts").fetchone()
            self.assertEqual(integration["ftp_directory"], "/routeros/backups")
            self.assertNotIn("Senha", integration["encrypted_ftp_secret"])
        status, _, detail = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Conta FTP", detail)
        self.assertIn("FTP Push vinculado ao equipamento", detail)
        self.assertIn("Gerenciar FTP", detail)
        self.assertNotIn(account["password_hash_or_secret_reference"], detail)
        self.assertNotIn("ROTATE-CREDENTIAL-TO-GENERATE-DEPLOYABLE-SCRIPT", detail)
        with mock.patch("backup_manager.app.run_routeros_ftp_test", return_value={"command_executed": True}):
            status, _, test_body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/test")
        self.assertTrue(status.startswith("302"), (status, test_body))
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, headers, rotated = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/rotate")
        self.assertTrue(status.startswith("201"))
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("Credencial rotacionada", rotated)
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, _, removal = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/deactivate")
        self.assertTrue(status.startswith("201"))
        self.assertIn('name=&quot;backup-manager&quot;', removal)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT is_active FROM mikrotik_ftp_integrations").fetchone()[0], 0)

    def test_mikrotik_ftp_script_can_be_installed_with_existing_ssh_credential(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik'),name='MK SSH' WHERE id=1")
            create_credential(conn, 1, 1, "SSH principal", "admin", "SenhaSSH!2026", 22, "")
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, _, created = self.client.request("POST", "/equipment/1/mikrotik-ftp", {
                "credential_action": "isolated", "routeros_version": "7", "backup_format": "backup",
                "schedule_mode": "scheduled", "schedule_time": "03:30", "ftp_host": "192.0.2.20",
                "ftp_port": "21", "ftp_directory": "/",
            })
        self.assertTrue(status.startswith("302")); self.assertEqual(created, "")
        with connect() as conn:
            integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations").fetchone()
        status, headers, generated = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/script")
        self.assertTrue(status.startswith("201")); self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("192.0.2.20", generated); self.assertIn("03:30:00", generated)
        self.assertNotIn("Instalar automaticamente via SSH", generated)
        self.assertNotIn(">Concluir<", generated)
        self.assertIn("Ele não executa o teste FTP", generated)
        captured = {}

        def fake_install(equipment, credential, config, connect_timeout, command_timeout, **kwargs):
            captured.update(equipment=equipment, credential=credential, config=config,
                            connect_timeout=connect_timeout, command_timeout=command_timeout)
            return {"routeros_major": 7, "routeros_version": "7.21.4", "script_installed": True,
                    "scheduler_installed": True, "changed": True, "preflight": {"state": "installed_valid"}}

        with mock.patch("backup_manager.app.install_routeros_script", side_effect=fake_install):
            status, headers, _ = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/install-ssh")
        self.assertTrue(status.startswith("302"), status)
        self.assertEqual(captured["equipment"]["ssh_backup_driver"], "mikrotik_routeros")
        self.assertEqual(captured["credential"]["username"], "admin")
        self.assertEqual(captured["config"].ftp_host, "192.0.2.20")
        self.assertEqual(captured["config"].schedule_time, "03:30")
        with connect() as conn:
            audits = list(conn.execute("SELECT action,details FROM audit_log WHERE action LIKE 'mikrotik_ftp.ssh_%'"))
        self.assertEqual(audits[-1]["action"], "mikrotik_ftp.ssh_installed")
        self.assertNotIn("password", audits[-1]["details"].lower()); self.assertNotIn("SenhaSSH", audits[-1]["details"])

        failure = SSHBackupError("ROUTEROS_SYNTAX_ERROR", "Importação dos objetos: expected closing brace")
        failure.install_stage = "script"; failure.routeros_version = "7.21.4"
        with mock.patch("backup_manager.app.install_routeros_script", side_effect=failure):
            status, _, failed_page = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/install-ssh")
        self.assertTrue(status.startswith("302"))

    def test_mikrotik_operations_have_json_contract_timeline_and_persistent_cards(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik'),name='MK feedback' WHERE id=1")
            create_credential(conn, 1, 1, "SSH", "admin", "SenhaSSH!2026", 22, "")
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            self.client.request("POST", "/equipment/1/mikrotik-ftp", {
                "credential_action": "isolated", "routeros_version": "7", "backup_format": "backup",
                "schedule_mode": "scheduled", "schedule_time": "03:30", "ftp_host": "192.0.2.20",
                "ftp_port": "21", "ftp_directory": "/",
            })
        with connect() as conn:
            integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations").fetchone()
        result = {"routeros_major": 7, "changed": False, "preflight": {"state": "installed_valid"}}
        with mock.patch("backup_manager.app.install_routeros_script", return_value=result):
            status, headers, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/install-ssh",
                                                        headers={"Accept": "application/json"})
        payload = json.loads(body)
        self.assertTrue(status.startswith("200")); self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual("installed_valid", payload["status"]); self.assertTrue(payload["message"])
        self.assertTrue(payload["operation_id"]); self.assertEqual(7, len(payload["timeline"]))
        self.assertIn("Nenhuma alteração foi necessária", payload["message"])

        for fake, expected_status, expected_message in (
            ({"routeros_major": 7, "changed": True, "preflight": {"state": "installed_valid"}},
             "installed", "Instalação concluída"),
            ({"routeros_major": 7, "changed": False, "needs_repair": True, "preflight": {"state": "needs_repair"}},
             "needs_repair", "Use Reparar instalação"),
        ):
            with mock.patch("backup_manager.app.install_routeros_script", return_value=fake):
                status, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/install-ssh",
                                                      headers={"Accept": "application/json"})
            case = json.loads(body)
            self.assertEqual(expected_status, case["status"]); self.assertIn(expected_message, case["message"])

        repair = {"routeros_major": 7, "changed": True, "preflight": {"state": "installed_valid"},
                  "preflight_before": {"state": "divergent", "script_valid": False, "scheduler_valid": True}}
        with mock.patch("backup_manager.app.install_routeros_script", return_value=repair):
            status, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/repair-ssh",
                                                  headers={"Accept": "application/json"})
        payload = json.loads(body)
        self.assertEqual("repaired", payload["status"])
        self.assertEqual("Instalação reparada e validada com sucesso.", payload["message"])
        self.assertIn("Correção aplicada: script atualizado", {item["step"] for item in payload["timeline"]})
        self.assertNotIn("Correção aplicada: scheduler atualizado", {item["step"] for item in payload["timeline"]})
        repair["changed"] = False
        with mock.patch("backup_manager.app.install_routeros_script", return_value=repair):
            _, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/repair-ssh",
                                             headers={"Accept": "application/json"})
        self.assertIn("Nenhuma reparação foi necessária", json.loads(body)["message"])

        with connect() as conn:
            conn.execute("UPDATE device_credentials SET port=222 WHERE equipment_id=?", (integration["equipment_id"],))
        with mock.patch("backup_manager.app.install_routeros_script",
                        side_effect=SSHBackupError("SSH_CONNECTION_REFUSED")):
            status, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/install-ssh",
                                                  headers={"Accept": "application/json"})
        refused = json.loads(body)
        self.assertTrue(status.startswith("502"))
        self.assertIn(":222", refused["message"])
        self.assertIn("serviço SSH", refused["message"])
        self.assertIn("firewall", refused["message"])
        with mock.patch("backup_manager.app.install_routeros_script", return_value=repair):
            status, headers, _ = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/repair-ssh")
        self.assertTrue(status.startswith("302")); self.assertEqual(f"/equipment/{integration['equipment_id']}", headers["Location"])
        _, _, first_page = self.client.request("GET", headers["Location"])
        _, _, second_page = self.client.request("GET", headers["Location"])
        flash_text = "Backup Manager já estava instalado e atualizado. Nenhuma reparação foi necessária."
        self.assertEqual(2, first_page.count(flash_text)); self.assertEqual(1, second_page.count(flash_text))

        expected_base = "mk-managed.2026-07-19-16-25-00.1"
        with mock.patch("backup_manager.app.run_routeros_managed_backup", return_value={"routeros_major": 7, "expected_base": expected_base}):
            status, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/execute-ssh",
                                                  headers={"Accept": "application/json"})
        execution = json.loads(body)
        self.assertTrue(status.startswith("200")); self.assertEqual("waiting_upload", execution["status"])
        self.assertFalse(execution["terminal"]); self.assertIn("status_url", execution)
        status, _, body = self.client.request("GET", execution["status_url"], headers={"Accept": "application/json"})
        self.assertEqual("waiting_upload", json.loads(body)["status"])
        with connect() as conn:
            conn.execute("""INSERT INTO mikrotik_ftp_uploads(uuid,integration_id,equipment_id,original_filename,
                file_type,routeros_major_version,backup_format,status,is_test) VALUES(?,?,?,?,?,7,?,'success',0)""",
                (str(uuid.uuid4()), integration["id"], integration["equipment_id"], "old-delayed.backup", "backup", "backup"))
        status, _, body = self.client.request("GET", execution["status_url"], headers={"Accept": "application/json"})
        self.assertEqual("waiting_upload", json.loads(body)["status"])
        with connect() as conn:
            conn.execute("""INSERT INTO mikrotik_ftp_uploads(uuid,integration_id,equipment_id,original_filename,
                file_type,routeros_major_version,backup_format,status,is_test) VALUES(?,?,?,?,?,7,?,'success',0)""",
                (str(uuid.uuid4()), integration["id"], integration["equipment_id"], f"{expected_base}.backup", "backup", "backup"))
        status, _, body = self.client.request("GET", execution["status_url"], headers={"Accept": "application/json"})
        receipt = json.loads(body)
        self.assertTrue(status.startswith("200")); self.assertEqual("validated", receipt["status"])
        self.assertTrue(receipt["terminal"])

        with mock.patch("backup_manager.app.run_routeros_ftp_test", return_value={"command_executed": True}):
            status, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/test",
                                                  headers={"Accept": "application/json"})
        waiting = json.loads(body)
        self.assertTrue(status.startswith("202")); self.assertEqual("waiting_upload", waiting["status"])
        self.assertFalse(waiting["terminal"]); self.assertTrue(waiting["message"])
        poll_path = f"/mikrotik-ftp/tests/{waiting['operation_uuid']}"
        status, _, body = self.client.request("GET", poll_path, headers={"Accept": "application/json"})
        self.assertEqual("waiting_upload", json.loads(body)["status"])
        with connect() as conn:
            conn.execute("UPDATE mikrotik_ftp_tests SET status='validated',validated_at=CURRENT_TIMESTAMP,completed_at=CURRENT_TIMESTAMP WHERE id=?", (waiting["operation_id"],))
        status, _, body = self.client.request("GET", poll_path, headers={"Accept": "application/json"})
        terminal = json.loads(body)
        self.assertEqual("validated", terminal["status"]); self.assertTrue(terminal["terminal"])
        self.assertEqual("success", terminal["canonical_status"])
        self.assertEqual("Envio FTP validado com sucesso.", terminal["message"])
        self.assertEqual("Funcionando", terminal["user_status"])
        self.assertIn("finished_at", terminal); self.assertFalse(terminal["retry_allowed"])

        with mock.patch("backup_manager.app.run_routeros_ftp_test",
                        side_effect=SSHBackupError("ROUTEROS_SCRIPT_FAILED", "backup file unavailable")):
            status, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/test",
                                                  headers={"Accept": "application/json"})
        failed_probe = json.loads(body)
        self.assertTrue(status.startswith("502"))
        self.assertEqual("backup_creation_failed", failed_probe["error_code"])
        self.assertIn("backup file unavailable", failed_probe["message"])
        self.assertEqual("Com problema", failed_probe["user_status"])
        self.assertTrue(failed_probe["retry_allowed"])

        with mock.patch("backup_manager.app.run_routeros_ftp_test",
                        side_effect=SSHBackupError("FTP_AUTH_FAILED", "failure: invalid user name or password")):
            status, _, body = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/test",
                                                  headers={"Accept": "application/json"})
        auth_failure = json.loads(body)
        self.assertTrue(status.startswith("502"))
        self.assertEqual("FTP_AUTH_FAILED", auth_failure["error_code"])
        self.assertIn("usuário e a senha FTP", auth_failure["message"])
        self.assertIn("nova senha FTP", auth_failure["action_recommendation"])
        self.assertNotIn("invalid user name or password", body)

        poll_expired_uuid = str(uuid.uuid4())
        with connect() as conn:
            conn.execute("""INSERT INTO mikrotik_ftp_tests(uuid,integration_id,status,expires_at,started_at,expected_filename,last_check_at)
                            VALUES(?,?,?,?,?,?,?)""",
                         (poll_expired_uuid, integration["id"], "waiting_upload", "2000-01-01 00:00:00",
                          "2000-01-01 00:00:00", "backup-manager-test-1.txt", "2000-01-01 00:00:00"))
        status, _, body = self.client.request("GET", f"/mikrotik-ftp/tests/{poll_expired_uuid}",
                                              headers={"Accept": "application/json"})
        expired_poll = json.loads(body)
        self.assertEqual("expired", expired_poll["status"]); self.assertTrue(expired_poll["terminal"])
        self.assertEqual("upload_timeout", expired_poll["error_code"])
        self.assertNotIn("pending", {item["status"] for item in expired_poll["timeline"]})

        for database_status, api_status, expected_message in (
            ("expired", "expired", "não recebeu o arquivo dentro do prazo"),
            ("failed", "failed", "não conseguiu executar"),
        ):
            test_uuid = str(uuid.uuid4())
            with connect() as conn:
                conn.execute("""INSERT INTO mikrotik_ftp_tests(uuid,integration_id,status,expires_at,error_code,error_message)
                              VALUES(?,?,?,?,?,?)""", (test_uuid, integration["id"], database_status,
                              "2099-01-01 00:00:00", "mock_error", "mensagem interna"))
            _, _, body = self.client.request("GET", f"/mikrotik-ftp/tests/{test_uuid}",
                                             headers={"Accept": "application/json"})
            case = json.loads(body)
            self.assertEqual(api_status, case["status"]); self.assertFalse(case["ok"])
            self.assertIn(expected_message, case["message"])

        status, _, page = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200")); self.assertIn("Última operação de instalação", page)
        self.assertIn("Último teste FTP", page); self.assertIn("O MikroTik não conseguiu executar o envio FTP.", page)
        self.assertIn('data-operation-result="ftp"', page)
        self.assertIn("data-operation-recommendation", page)
        self.assertIn("data-operation-progress", page)
        self.assertIn('id="mikrotik-ftp-test-progress"', page)
        self.assertIn('data-state="ready"', page)
        self.assertIn("Iniciar teste FTP", page)
        self.assertIn(">Concluído</strong>", page)
        self.assertNotIn(">completed</strong>", page)
        self.assertNotIn("SenhaSSH!2026", page)

        status, headers, script_text = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/script",
                                                            headers={"Accept": "text/plain"})
        self.assertTrue(status.startswith("200")); self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("text/plain", headers["Content-Type"]); self.assertIn("backup-manager-version", script_text)
        self.assertNotIn("ROTATE-CREDENTIAL-TO-GENERATE-DEPLOYABLE-SCRIPT", script_text)
        self.assertIn('id="mikrotik-manual-script"', page); self.assertIn("data-manual-script-form", page)
        self.assertIn('data-script-version="5.8"', page)
        for removed in ("Instalar automaticamente via SSH", ">Concluir<", "Verificar teste"):
            self.assertNotIn(removed, page)

    def test_mikrotik_frontend_handles_ajax_polling_and_visible_errors(self) -> None:
        self.login_ready_admin()
        status, _, javascript = self.client.request("GET", "/static/app.js")
        self.assertTrue(status.startswith("200"))
        for expected in ("form[data-mikrotik-operation]", "response.redirected", "application/json",
                         "JSON inválido", "data-operation-timeline", "pollMikrotikFtp", "fetchOperation",
                         "AbortController", "O servidor demorou demais para responder", "finally",
                         'replace(/\\/(?:install|repair)-ssh$/, "/execute-ssh")'):
            self.assertIn(expected, javascript)

    def test_equipment_detail_has_four_simple_areas_and_hides_advanced_controls(self) -> None:
        self.login_ready_admin()
        status, _, page = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        for tab in ("resumo", "backup", "historico", "diagnostico"):
            self.assertIn(f'data-equipment-tab-link="{tab}"', page)
            self.assertIn(f'data-equipment-tab-panel="{tab}"', page)
        for label in ("Resumo geral", "Histórico", "Diagnóstico", "Configuração avançada"):
            self.assertIn(label, page)
        for section in ("Acesso SSH", "Envio FTP", "Agendamento", "Credencial SSH",
                        "Informações principais", "Ações rápidas", "Diagnóstico simplificado",
                        "Últimos backups", "Próximos passos recomendados"):
            self.assertIn(section, page)
        self.assertIn('class="equipment-ui-icon"', page)
        for heading in ("Backup e integrações", "Consulte arquivos, datas, métodos",
                        "Acesso e integridade"):
            self.assertIn(heading, page)
        self.assertIn("Executar backup agora", page)
        self.assertIn("Testar conexão", page)
        self.assertIn("Gerenciar backup", page)
        self.assertIn('data-equipment-tab-panel="diagnostico" hidden', page)
        diagnostic = page.split('data-equipment-tab-panel="diagnostico"', 1)[1]
        for diagnostic_section in ("Estado da conexão", "Credencial ativa", "Último diagnóstico",
                                   "Ver detalhes do diagnóstico"):
            self.assertIn(diagnostic_section, diagnostic)
        for moved_section in ("Área técnica", "Recebimento FTP", "Importar backup", "Agendamentos de backup"):
            self.assertNotIn(moved_section, diagnostic)
        backup_area = page.split('data-equipment-tab-panel="backup"', 1)[1].split(
            'data-equipment-tab-panel="historico"', 1)[0]
        for advanced_section in ("Conta FTP", "Agendamentos de backup", "Criar agendamento"):
            self.assertIn(advanced_section, backup_area)
        for removed_section in ("Importação manual", "Zona de perigo"):
            self.assertNotIn(removed_section, backup_area)
        self.assertNotIn("<h2>Backup automático</h2>", backup_area)

        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik') WHERE id=1")
        status, _, mikrotik = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        backup_area = mikrotik.split('data-equipment-tab-panel="backup"', 1)[1].split(
            'data-equipment-tab-panel="historico"', 1)[0]
        self.assertIn("Agendamentos de backup", backup_area)
        self.assertIn("Não configurado", backup_area)
        self.assertIn("Criar agendamento", backup_area)

    def test_routeros_ssh_installer_executes_and_verifies_generated_script(self) -> None:
        self.login_ready_admin()
        class Stream:
            def __init__(self, value=b""): self.value = value
            def read(self): return self.value

        class Client:
            def __init__(self): self.commands = []; self.connected = {}; self.closed = False; self.files = {}; self.installed = False; self.config_marker = ""
            def connect(self, **kwargs): self.connected = kwargs
            def open_sftp(self):
                owner = self
                class SFTP:
                    def putfo(self, stream, path): owner.files[path] = stream.read()
                return SFTP()
            def exec_command(self, command, timeout=60):
                self.commands.append(command)
                if command == ":put [/system resource get version]": output = b"7.21.4 (stable)\n"
                elif command.startswith('/import file-name="backup-manager-install-'):
                    self.installed = True; output = b"Script file loaded and executed successfully\n"
                elif command.startswith('/file print count-only where name="backup-manager-'): output = b"1\n"
                elif "print count-only" in command: output = b"1\n" if self.installed else b"0\n"
                elif command == '/system script print detail where name="backup-manager"': output = f'0 name="backup-manager" source="# backup-manager-version=5.8 # backup-manager-config={self.config_marker}"\n'.encode()
                elif command == '/system scheduler print detail where name="backup-manager"': output = b'0 name="backup-manager" disabled=no interval=1d start-time=02:00:00 policy=ftp,read,write,policy,test,sensitive on-event="/system script run backup-manager"\n'
                else: output = b""
                return None, Stream(output), Stream()
            def close(self): self.closed = True

        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik') WHERE id=1")
            credential = create_credential(conn, 1, 1, "SSH", "admin", "SenhaSSH!2026", 22, "")
            equipment = conn.execute("SELECT * FROM equipment WHERE id=1").fetchone()
        client = Client()
        config = ScriptConfig(1, "router", 6, "backup", "scheduled", "daily", "02:00",
                              "192.0.2.20", 2121, "ftp-user", 'S$e"nha\\forte', "/backups")
        from backup_manager.mikrotik_ftp_scripts import config_fingerprint
        client.config_marker = config_fingerprint(config.__class__(**{**config.__dict__, "routeros_version": 7}))
        result = install_routeros_script(dict(equipment), dict(credential), config, 5, 30, client_factory=lambda: client)
        transferred_after_first = len(client.files)
        second = install_routeros_script(dict(equipment), dict(credential), config, 5, 30, client_factory=lambda: client)
        self.assertEqual(client.commands[0], ":put [/system resource get version]")
        self.assertEqual(result["routeros_major"], 7)
        self.assertEqual(second["routeros_major"], 7)
        self.assertEqual(len(client.files), transferred_after_first)
        self.assertGreaterEqual(sum(command == '/system script print count-only where name="backup-manager"' for command in client.commands), 2)
        self.assertTrue(any(b"bmPort 2121" in content for content in client.files.values()))
        self.assertFalse(any(b"backup-manager-ftp-test" in content for content in client.files.values()))
        self.assertEqual(client.connected["username"], "admin"); self.assertEqual(client.connected["password"], "SenhaSSH!2026")
        self.assertTrue(client.closed)

        class PositiveAfterErrorClient(Client):
            def exec_command(self, command, timeout=60):
                if command.startswith('/import file-name="backup-manager-install-'):
                    self.commands.append(command)
                    return None, Stream(b"expected closing brace\nbackup-manager ftp objects installed\n"), Stream()
                return super().exec_command(command, timeout)

        with self.assertRaises(SSHBackupError) as syntax_failure:
            install_routeros_script(dict(equipment), dict(credential), config, 5, 30,
                                    client_factory=PositiveAfterErrorClient)
        self.assertEqual(syntax_failure.exception.code, "ROUTEROS_SYNTAX_ERROR")

        class ZeroObjectsClient(Client):
            def exec_command(self, command, timeout=60):
                if command.startswith('/import file-name="backup-manager-install-'):
                    self.commands.append(command)
                    return None, Stream(b"Script file loaded and executed successfully\n"), Stream()
                return super().exec_command(command, timeout)

        with self.assertRaises(SSHBackupError) as missing_objects:
            install_routeros_script(dict(equipment), dict(credential), config, 5, 30,
                                    client_factory=ZeroObjectsClient)
        self.assertEqual(missing_objects.exception.code, "ROUTEROS_SCRIPT_FAILED")

    def test_mikrotik_ftp_reuses_legacy_account_without_changing_it(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik'),name='MK-TESTE' WHERE id=1")
            account = create_account(conn, equipment_id=1, name="Importador antigo", username="mklegado",
                                     password="SenhaLegada!1", created_by_user_id=1)
            conn.execute("UPDATE ftp_accounts SET last_upload_at='2026-07-11 00:41:28',last_upload_status='imported' WHERE id=?", (account["id"],))
            before = dict(conn.execute("SELECT * FROM ftp_accounts WHERE id=?", (account["id"],)).fetchone())
        status, _, page = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200")); self.assertIn("Conta FTP pronta para uso", page)
        self.assertIn("m******o", page); self.assertIn("Último recebimento", page)
        self.assertNotIn("Importador FTP antigo", page); self.assertNotIn(account["uuid"], page)
        self.assertNotIn(account["home_relative_path"], page); self.assertNotIn("SenhaLegada!1", page)
        self.assertNotIn("Criar nova credencial isolada", page)
        status, headers, _ = self.client.request("POST", "/equipment/1/mikrotik-ftp", {
            "credential_action": "reuse", "routeros_version": "7", "backup_format": "backup",
            "schedule_mode": "manual", "schedule_time": "02:00", "ftp_host": "192.0.2.10",
            "ftp_port": "21", "ftp_directory": "/",
        })
        self.assertTrue(status.startswith("302")); self.assertIn("/equipment/1", headers["Location"])
        with connect() as conn:
            after = dict(conn.execute("SELECT * FROM ftp_accounts WHERE id=?", (account["id"],)).fetchone())
            integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations").fetchone()
            self.assertEqual(integration["ftp_account_id"], account["id"]); self.assertEqual(integration["encrypted_ftp_secret"], "")
            for key in ("uuid", "username", "home_relative_path", "password_hash_or_secret_reference", "is_active", "last_upload_at"):
                self.assertEqual(before[key], after[key])
        self.assertNotIn("SenhaLegada!1", headers["Location"])
        with mock.patch("backup_manager.app.run_helper") as helper:
            status, _, _ = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/deactivate")
        self.assertTrue(status.startswith("201")); helper.assert_not_called()
        with connect() as conn:
            self.assertEqual(1, conn.execute("SELECT is_active FROM ftp_accounts WHERE id=?", (account["id"],)).fetchone()[0])
            self.assertEqual("mikrotik_routeros", conn.execute("SELECT ssh_backup_driver FROM equipment WHERE id=1").fetchone()[0])

    def test_inactive_ftp_push_is_not_presented_as_manageable_installation(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik') WHERE id=1")
            old_account = create_account(conn, equipment_id=1, name="Antiga", username="ftpold1",
                                         password="OldPassword!1", is_active=False)
            conn.execute("UPDATE ftp_accounts SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (old_account["id"],))
            conn.execute("""INSERT INTO mikrotik_ftp_integrations
                (uuid,equipment_id,ftp_account_id,routeros_major_version,backup_format,schedule_mode,
                 schedule_frequency,schedule_time,ftp_host,ftp_port,ftp_directory,encrypted_ftp_secret,is_active)
                VALUES(?,?,?,?,?,'manual','daily','02:00','192.0.2.10',21,'/','',1)""",
                (str(uuid.uuid4()), 1, old_account["id"], 7, "both"))
            active_account = create_account(conn, equipment_id=1, name="Atual", username="ftpnew1",
                                            password="NewPassword!1")
        status, _, page = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Configurar FTP Push", page)
        self.assertIn("Conta pronta", page)
        self.assertIn("FTP Push ainda não configurado", page)
        self.assertNotIn("Conta vinculada ao equipamento", page)
        self.assertIn("Usar conta existente", page)
        self.assertIn("f*****1", page)
        self.assertNotIn("Instalação guiada no MikroTik", page)
        self.assertNotIn(f'/mikrotik-ftp/{old_account["id"]}/install-ssh', page)
        self.assertIsNotNone(active_account)
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, headers, _ = self.client.request("POST", "/equipment/1/mikrotik-ftp", {
                "credential_action": "rotate", "routeros_version": "7", "backup_format": "both",
                "schedule_mode": "manual", "schedule_time": "02:00", "ftp_host": "192.0.2.10",
                "ftp_port": "21", "ftp_directory": "/",
            })
        self.assertTrue(status.startswith("302")); self.assertEqual("/equipment/1", headers["Location"].split("?", 1)[0])
        with connect() as conn:
            integrations = conn.execute("SELECT ftp_account_id,is_active FROM mikrotik_ftp_integrations ORDER BY id").fetchall()
        self.assertEqual([(old_account["id"], 0), (active_account["id"], 1)], [(row[0], row[1]) for row in integrations])
        with connect() as conn:
            current = conn.execute("SELECT uuid FROM mikrotik_ftp_integrations WHERE is_active=1").fetchone()
            conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                VALUES(1,'mikrotik_ftp.ssh_install_failed','mikrotik_ftp_integration',?,?,'127.0.0.1')""",
                (current["uuid"], json.dumps({"installation_state": "unknown", "status": "failed",
                 "error_code": "validation_failed", "message": "A senha FTP não está disponível."})))
        status, _, page = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200")); self.assertIn("Configuração incompleta", page)
        self.assertIn("Atualizar senha FTP", page); self.assertNotIn("Estado desconhecido", page)
        with connect() as conn:
            conn.execute("""INSERT INTO audit_log(user_id,action,entity,entity_id,details,ip_address)
                VALUES(1,'mikrotik_ftp.credential_rotated','mikrotik_ftp_integration',?,'{}','127.0.0.1')""", (current["uuid"],))
        status, _, page = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200")); self.assertIn("Senha FTP atualizada", page)
        self.assertIn("Não instalado", page); self.assertNotIn("A senha FTP não está disponível", page)

    def test_mikrotik_ftp_recoverable_reference_and_rotation_emit_secret_once(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik') WHERE id=1")
            account = create_account(conn, equipment_id=1, name="Legada", username="mkrecover", password="OldPassword!1")
            conn.execute("UPDATE ftp_accounts SET password_hash_or_secret_reference=? WHERE id=?",
                         ("fernet:" + encrypt_secret("Recoverable!1"), account["id"]))
        form = {"credential_action": "reuse", "routeros_version": "6", "backup_format": "backup",
                "schedule_mode": "manual", "schedule_time": "02:00", "ftp_host": "192.0.2.44",
                "ftp_port": "2121", "ftp_directory": "/"}
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, headers, body = self.client.request("POST", "/equipment/1/mikrotik-ftp", form)
        self.assertTrue(status.startswith("302"))
        self.assertIn("notice=success", headers["Location"])
        self.assertNotIn("Recoverable!1", body)
        status, _, page = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200")); self.assertNotIn("Recoverable!1", page)
        with connect() as conn:
            integration = conn.execute("SELECT * FROM mikrotik_ftp_integrations").fetchone()
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, headers, rotated = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/rotate")
        self.assertTrue(status.startswith("201")); self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("Credencial rotacionada", rotated); self.assertNotIn("Recoverable!1", rotated)
        with mock.patch("backup_manager.app.run_routeros_ftp_test", return_value={"command_executed": True}):
            status, _, test_script = self.client.request("POST", f"/mikrotik-ftp/{integration['id']}/test")
        self.assertTrue(status.startswith("302")); self.assertNotIn("Recoverable!1", test_script)
        with connect() as conn:
            audit_text = " ".join(row[0] for row in conn.execute("SELECT details FROM audit_log"))
        self.assertNotIn("Recoverable!1", audit_text); self.assertNotIn("Senha FTP", audit_text)

    def test_mikrotik_ftp_inactive_and_duplicate_account_require_safe_action(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='mikrotik_routeros',vendor_id=(SELECT id FROM vendors WHERE name='MikroTik') WHERE id=1")
            account = create_account(conn, equipment_id=1, name="Inativa", username="mkinactive", password="Inactive!1")
            conn.execute("UPDATE ftp_accounts SET is_active=0 WHERE id=?", (account["id"],))
        common = {"routeros_version": "7", "backup_format": "backup", "schedule_mode": "manual",
                  "schedule_time": "02:00", "ftp_host": "192.0.2.10", "ftp_port": "21", "ftp_directory": "/"}
        status, _, page = self.client.request("POST", "/equipment/1/mikrotik-ftp", {**common, "credential_action": "reuse"})
        self.assertTrue(status.startswith("200")); self.assertIn("não possui conta FTP para reutilizar", page)
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "ok")):
            status, _, page = self.client.request("POST", "/equipment/1/mikrotik-ftp", {**common, "credential_action": "isolated"})
        self.assertTrue(status.startswith("302")); self.assertIn("notice=success", _["Location"])
        with connect() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM ftp_accounts").fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM mikrotik_ftp_integrations WHERE is_active=1").fetchone()[0])

    def test_mikrotik_management_discovery_legacy_vendor_permissions_and_generic(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            mikrotik_vendor = conn.execute("SELECT id FROM vendors WHERE name='MikroTik'").fetchone()[0]
            generic_vendor = conn.execute("SELECT id FROM vendors WHERE name!='MikroTik' ORDER BY id LIMIT 1").fetchone()[0]
            conn.execute("UPDATE equipment SET name='MikroTik por fabricante',ssh_backup_driver='generic_ssh',vendor_id=? WHERE id=1", (mikrotik_vendor,))
            conn.execute("""INSERT INTO equipment(id,name,hostname,ip_address,vendor_id,ssh_backup_driver,ssh_port)
                          VALUES(2,'MikroTik legado','mt-legacy','192.0.2.2',?,'routeros',22)""", (generic_vendor,))
            conn.execute("""INSERT INTO equipment(id,name,hostname,ip_address,vendor_id,ssh_backup_driver,ssh_port)
                          VALUES(3,'MikroTik explícito','mt-explicit','192.0.2.3',?,'mikrotik_routeros',22)""", (generic_vendor,))
            conn.execute("""INSERT INTO equipment(id,name,hostname,ip_address,vendor_id,ssh_backup_driver,ssh_port)
                          VALUES(4,'SSH genérico','generic-ssh','192.0.2.4',?,'generic_ssh',22)""", (generic_vendor,))
            conn.execute("""INSERT INTO equipment(id,name,hostname,ip_address,vendor_id,ssh_backup_driver,ssh_port)
                          VALUES(5,'MikroTik válido','mt-valid','192.0.2.5',?,'mikrotik_routeros',22)""", (mikrotik_vendor,))

        status, _, listing = self.client.request("GET", "/equipment")
        self.assertTrue(status.startswith("200"))
        self.assertIn('/equipment/5#ftp-push', listing)
        for equipment_id in (1, 2, 3, 4):
            self.assertNotIn(f'/equipment/{equipment_id}#ftp-push', listing)
        self.assertIn('/equipment/1/edit', listing)
        self.assertIn('action="/equipment/1/purge"', listing)

        status, _, detail = self.client.request("GET", "/equipment/5")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Não configurado", detail)
        self.assertIn('id="mikrotik-ftp-config"', detail)
        advanced = detail.split('data-equipment-tab-panel="backup"', 1)[1].split('data-equipment-tab-panel="historico"', 1)[0]
        self.assertIn("Configurar FTP Push", advanced)
        diagnostic = detail.split('data-equipment-tab-panel="diagnostico"', 1)[1]
        self.assertNotIn('id="mikrotik-ftp-config"', diagnostic)
        self.assertIn('name="ftp_directory"', detail)
        self.assertNotRegex(detail, r'type="password"[^>]*value=')
        status, _, generic_detail = self.client.request("GET", "/equipment/4")
        self.assertTrue(status.startswith("200"))
        self.assertNotIn('data-mikrotik-ftp-panel', generic_detail)

        anonymous = WsgiClient()
        status, headers, _ = anonymous.request("GET", "/equipment/5")
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/login")

        with connect() as conn:
            conn.execute("INSERT INTO users(username,full_name,password_hash,role_id,must_change_password) VALUES(?,?,?,(SELECT id FROM roles WHERE slug='read_download'),0)",
                         ("ftpviewer", "FTP Viewer", hash_password("ViewerSenha!2026")))
        viewer = WsgiClient()
        viewer.request("POST", "/login", {"username": "ftpviewer", "password": "ViewerSenha!2026"})
        status, _, viewer_detail = viewer.request("GET", "/equipment/5")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Não configurado", viewer_detail)
        self.assertNotIn('id="mikrotik-ftp-config"', viewer_detail)
        status, _, _ = viewer.request("POST", "/equipment/5/mikrotik-ftp", {
            "routeros_version": "7", "backup_format": "backup", "schedule_mode": "manual",
            "schedule_time": "02:00", "ftp_host": "192.0.2.10", "ftp_port": "21", "ftp_directory": "/",
        })
        self.assertTrue(status.startswith("403"))
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mikrotik_ftp_integrations").fetchone()[0], 0)

    def test_lifecycle_simulation_cleanup_restore_health_and_interface(self) -> None:
        self.login_ready_admin()
        for index in range(3):
            status, _, _ = self.client.multipart("/equipment/1/backups/import", {}, f"retention-{index}.cfg", f"content-{index}".encode())
            self.assertTrue(status.startswith("302"))
        with connect() as conn:
            rows = conn.execute("SELECT * FROM backups ORDER BY id").fetchall()
            original_hashes = {row["uuid"]: row["sha256"] for row in rows}
            save_policy(conn, equipment_id=None, max_count=1, max_age_days=0, trash_days=2, rejected_days=3)
            save_policy(conn, equipment_id=1, max_count=2, max_age_days=None, trash_days=None, rejected_days=None)
            self.assertEqual(effective_policy(conn, 1).max_count, 2)
            save_policy(conn, equipment_id=1, max_count=1, max_age_days=None, trash_days=None, rejected_days=None)
            report = simulate(conn, user_id=1)
            self.assertEqual(report["candidate_count"], 2)
            self.assertGreater(report["candidate_bytes"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups WHERE backup_status='available'").fetchone()[0], 3)
            with self.assertRaises(ValueError):
                execute(conn, confirmation="nao", user_id=1)
            result = execute(conn, confirmation=CONFIRMATION, user_id=1)
            self.assertEqual(result["moved_count"], 2)
            trashed = conn.execute("SELECT * FROM backups WHERE backup_status='trashed' ORDER BY id LIMIT 1").fetchone()
            self.assertEqual(trashed["sha256"], original_hashes[trashed["uuid"]])
            restore(conn, trashed["uuid"], user_id=1)
            restored = conn.execute("SELECT * FROM backups WHERE uuid=?", (trashed["uuid"],)).fetchone()
            self.assertEqual(restored["backup_status"], "available")
            self.assertEqual(restored["sha256"], original_hashes[restored["uuid"]])
            health = health_check(conn, include_hash=True, user_id=1)
            self.assertTrue(health["ok"])
            account_uuid = str(uuid.uuid4())
            received_uuid = str(uuid.uuid4())
            cursor = conn.execute("""INSERT INTO ftp_accounts(uuid,equipment_id,name,username,password_hash_or_secret_reference,
                home_relative_path,permission_mode,is_active) VALUES(?,1,'retention test','retention-test','hash',?,'upload_only',1)""",
                (account_uuid, f"ftp-incoming/accounts/{account_uuid}/incoming"))
            conn.execute("""INSERT INTO ftp_received_files(uuid,ftp_account_id,equipment_id,original_filename,incoming_relative_path,
                status,file_size,processed_at) VALUES(?,?,1,'bad.exe','bad.exe','rejected',4,'2000-01-01 00:00:00')""",
                (received_uuid, cursor.lastrowid))
            rejected_dir = Path(os.environ["BACKUP_MANAGER_STORAGE_ROOT"]) / "ftp-incoming" / "accounts" / account_uuid / "rejected"
            rejected_dir.mkdir(parents=True)
            (rejected_dir / f"{received_uuid}-bad.exe").write_bytes(b"nope")
            conn.execute("UPDATE backups SET trash_expires_at='2000-01-01 00:00:00' WHERE backup_status='trashed'")
            purged = execute(conn, confirmation="", automatic=True)
            self.assertEqual(purged["purged_count"], 1)
            self.assertEqual(purged["rejected_count"], 1)
            rejected = conn.execute("SELECT lifecycle_trash_relative_path FROM ftp_received_files WHERE uuid=?", (received_uuid,)).fetchone()
            self.assertTrue(rejected["lifecycle_trash_relative_path"])
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE action LIKE 'lifecycle.%'").fetchone()[0], 4)

    def test_admin_can_purge_selected_backup_or_empty_only_the_controlled_trash(self) -> None:
        self.login_ready_admin()
        for name in ("purge-one.cfg", "purge-two.cfg", "purge-three.cfg"):
            self.client.multipart("/equipment/1/backups/import", {}, name, name.encode())
        with connect() as conn:
            rows = conn.execute("SELECT uuid FROM backups ORDER BY id DESC LIMIT 3").fetchall()
        for row in rows:
            self.client.request("POST", f"/backups/{row['uuid']}/trash")

        status, _, page = self.client.request("GET", "/lifecycle")
        self.assertTrue(status.startswith("200")); self.assertIn("Apagar definitivamente", page)
        self.assertIn(EMPTY_TRASH_CONFIRMATION, page)

        first_uuid = rows[0]["uuid"]
        self.client.request("POST", f"/backups/{first_uuid}/purge", {"confirmation": "errado"})
        with connect() as conn:
            self.assertEqual("trashed", conn.execute("SELECT backup_status FROM backups WHERE uuid=?", (first_uuid,)).fetchone()[0])
        self.client.request("POST", f"/backups/{first_uuid}/purge", {"confirmation": PURGE_CONFIRMATION})
        with connect() as conn:
            purged = conn.execute("SELECT backup_status,relative_path,trash_relative_path FROM backups WHERE uuid=?", (first_uuid,)).fetchone()
            self.assertEqual(("deleted", None, None), tuple(purged))
            self.assertEqual("lifecycle.backup_purged", conn.execute("SELECT action FROM audit_log ORDER BY id DESC").fetchone()[0])

        self.client.request("POST", "/lifecycle/empty-trash", {"confirmation": "errado"})
        with connect() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM backups WHERE backup_status='trashed'").fetchone()[0])
        self.client.request("POST", "/lifecycle/empty-trash", {"confirmation": EMPTY_TRASH_CONFIRMATION})
        with connect() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM backups WHERE backup_status='trashed'").fetchone()[0])
            self.assertEqual(3, conn.execute("SELECT COUNT(*) FROM backups WHERE backup_status='deleted'").fetchone()[0])
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

        status, _, body = self.client.request("GET", "/lifecycle")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Retenção e limpeza", body)
        self.assertIn("Simular sem alterar arquivos", body)
        status, headers, _ = self.client.request("POST", "/lifecycle/execute", {"confirmation": "incorreta"})
        self.assertTrue(status.startswith("302"))
        self.assertIn("notice=error", headers["Location"])

    def test_ftp_account_password_is_one_time_and_username_is_validated(self) -> None:
        self.login_ready_admin()
        password = "SenhaFTP-Forte-2026"
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "account_synced")):
            status, _, body = self.client.request("POST", "/ftp", {"name": "FTP Router Edge", "equipment_id": "1", "username": "router-edge-ftp", "password": password, "confirm_password": password, "permission_mode": "upload_only", "allowed_source": "10.0.0.2/32", "quota_bytes": "1048576", "max_files": "10", "is_active": "1", "notes": ""})
        self.assertTrue(status.startswith("201")); self.assertIn(password, body)
        status, _, detail = self.client.request("GET", "/ftp/1")
        self.assertTrue(status.startswith("200")); self.assertNotIn(password, detail)
        with connect() as conn:
            row = conn.execute("SELECT * FROM ftp_accounts WHERE id=1").fetchone()
            self.assertNotEqual(row["password_hash_or_secret_reference"], password); self.assertIn(row["uuid"], row["home_relative_path"])
        for invalid in ("root", "Admin", "../escape", "ab"):
            with self.assertRaises(FTPAccountError): validate_username(invalid)

    def test_ftp_account_settings_can_be_edited_and_roll_back_when_helper_fails(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            account = create_account(conn, equipment_id=1, name="FTP editável", username="ftp-editavel",
                                     password="SenhaFTP-Forte-2026", created_by_user_id=1)
            account_id = account["id"]
        status, _, detail = self.client.request("GET", f"/ftp/{account_id}")
        self.assertTrue(status.startswith("200")); self.assertIn("Configuração de recebimento", detail)
        self.assertIn('class="ftp-edit-settings-button"', detail)
        self.assertIn('class="ftp-disable-account-button"', detail)
        self.assertIn('data-dialog-open="ftp-account-password"', detail)
        self.assertIn('id="ftp-account-password"', detail)
        self.assertIn('data-dialog-open="ftp-account-delete"', detail)
        self.assertIn('id="ftp-account-delete"', detail)
        with mock.patch("backup_manager.app.run_helper", return_value=(True, "account_ok")) as helper:
            status, headers, _ = self.client.request("POST", f"/ftp/{account_id}/settings", {
                "allowed_source": "10.20.30.0/24", "upload_subdirectory": "mikrotik",
                "quota_bytes": "2048", "max_files": "20", "notes": "Backups do roteador",
            })
        self.assertTrue(status.startswith("302")); self.assertIn(f"/ftp/{account_id}", headers["Location"])
        helper.assert_called_once_with("check-account", account_id)
        with connect() as conn:
            updated = conn.execute("SELECT * FROM ftp_accounts WHERE id=?", (account_id,)).fetchone()
            self.assertEqual("10.20.30.0/24", updated["allowed_source_cidr"])
            self.assertEqual("mikrotik", updated["upload_subdirectory"])
            self.assertEqual(2048, updated["quota_bytes"]); self.assertEqual(20, updated["max_files"])
        with mock.patch("backup_manager.app.run_helper", return_value=(False, "helper_failed")):
            self.client.request("POST", f"/ftp/{account_id}/settings", {
                "allowed_source": "192.0.2.1", "upload_subdirectory": "outra",
                "quota_bytes": "4096", "max_files": "40", "notes": "Não deve persistir",
            })
        with connect() as conn:
            rolled_back = conn.execute("SELECT * FROM ftp_accounts WHERE id=?", (account_id,)).fetchone()
            self.assertEqual("10.20.30.0/24", rolled_back["allowed_source_cidr"])
            self.assertEqual("mikrotik", rolled_back["upload_subdirectory"])

    def test_ftp_stable_file_is_imported_and_audited(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            account = create_account(conn, equipment_id=1, name="Conta laboratorio", username="lab-ftp", password="SenhaFTP-Forte-2026", created_by_user_id=1)
            conn.execute("UPDATE settings SET value='0' WHERE key='ftp_stable_seconds'")
            incoming = os.path.join(os.environ["BACKUP_MANAGER_STORAGE_ROOT"], account["home_relative_path"]); os.makedirs(incoming, exist_ok=True)
            with open(os.path.join(incoming, "config.cfg"), "wb") as handle: handle.write(b"hostname lab\n")
            first = scan_once(conn); second = scan_once(conn)
            self.assertEqual(first.detected, 1); self.assertEqual(second.imported, 1)
            backup = conn.execute("SELECT * FROM backups WHERE source_method='ftp'").fetchone()
            self.assertEqual(backup["backup_reason"], "event"); self.assertEqual(backup["backup_status"], "available"); self.assertEqual(len(backup["sha256"]), 64)
            self.assertFalse(os.path.exists(os.path.join(incoming, "config.cfg")))
            actions = {row[0] for row in conn.execute("SELECT action FROM audit_log")}
            self.assertIn("ftp.upload_imported", actions); self.assertIn("backup.created_from_ftp", actions)

    def test_ftp_empty_invalid_and_symlink_are_rejected(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            account = create_account(conn, equipment_id=1, name="Conta laboratorio", username="lab-ftp", password="SenhaFTP-Forte-2026", created_by_user_id=1)
            conn.execute("UPDATE settings SET value='0' WHERE key='ftp_stable_seconds'")
            incoming = os.path.join(os.environ["BACKUP_MANAGER_STORAGE_ROOT"], account["home_relative_path"]); os.makedirs(incoming, exist_ok=True)
            open(os.path.join(incoming, "empty.cfg"), "wb").close()
            with open(os.path.join(incoming, "bad.exe"), "wb") as handle: handle.write(b"bad")
            os.symlink("bad.exe", os.path.join(incoming, "link.cfg"))
            scan_once(conn); result = scan_once(conn)
            self.assertEqual(result.rejected, 3); self.assertEqual(conn.execute("SELECT COUNT(*) FROM backups WHERE source_method='ftp'").fetchone()[0], 0)

    def test_jobs_scheduler_worker_and_permissions(self) -> None:
        self.login_ready_admin()
        status, _, body = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Criar agendamento", body)

        status, _, _ = self.client.request("POST", "/equipment/1/jobs/run-now")
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            run = conn.execute("SELECT backup_job_runs.*, backup_jobs.uuid AS job_uuid FROM backup_job_runs JOIN backup_jobs ON backup_jobs.id = backup_job_runs.job_id").fetchone()
        self.assertEqual(run["status"], "queued")

        with connect() as conn:
            result = run_once(conn)
            completed = conn.execute("SELECT * FROM backup_job_runs WHERE uuid = ?", (run["uuid"],)).fetchone()
        self.assertEqual(result.success_runs, 1)
        self.assertEqual(completed["status"], "success")
        self.assertIn("Nenhuma conexao externa", completed["safe_log"])
        self.assertNotIn("password", completed["safe_log"].lower())

        status, _, body = self.client.request("GET", "/jobs")
        self.assertTrue(status.startswith("200"))
        self.assertIn("router-edge-01", body)
        status, _, body = self.client.request("GET", "/job-runs")
        self.assertTrue(status.startswith("200"))
        self.assertIn("success", body)
        status, _, body = self.client.request("GET", f"/job-runs/{run['uuid']}")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Log seguro", body)

        status, _, _ = self.client.request(
            "POST",
            "/equipment/1/jobs",
            {"method": "dry_run", "schedule_type": "daily", "schedule_time": "03:15", "notes": "simulate_failure"},
        )
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            daily = conn.execute("SELECT * FROM backup_jobs WHERE schedule_type = 'daily'").fetchone()
        self.assertTrue(daily["next_run_at"])

        self.client.request("POST", f"/jobs/{daily['uuid']}/run")
        with connect() as conn:
            result = run_once(conn)
            failed = conn.execute("SELECT * FROM backup_job_runs WHERE job_id = ? ORDER BY id DESC LIMIT 1", (daily["id"],)).fetchone()
        self.assertEqual(result.failed_runs, 1)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "DRY_RUN_SIMULATED_FAILURE")
        self.assertNotIn("Traceback", failed["safe_log"])

        status, _, _ = self.client.request(f"POST", f"/jobs/{daily['uuid']}/pause")
        self.assertTrue(status.startswith("302"))
        status, _, _ = self.client.request("POST", f"/jobs/{daily['uuid']}/run")
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            queued_after_pause = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE job_id = ? AND status = 'queued'", (daily["id"],)).fetchone()[0]
        self.assertEqual(queued_after_pause, 0)

        status, _, _ = self.client.request("POST", f"/jobs/{daily['uuid']}/resume")
        self.assertTrue(status.startswith("302"))
        self.client.request("POST", f"/jobs/{daily['uuid']}/run")
        self.client.request("POST", f"/jobs/{daily['uuid']}/run")
        with connect() as conn:
            queued = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE job_id = ? AND status = 'queued'", (daily["id"],)).fetchone()[0]
        self.assertEqual(queued, 1)
        with connect() as conn:
            run_once(conn)

        status, _, _ = self.client.request(
            "POST",
            "/equipment/1/jobs",
            {"method": "dry_run", "schedule_type": "weekly", "schedule_time": "04:20", "schedule_days": "0", "notes": ""},
        )
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            weekly = conn.execute("SELECT * FROM backup_jobs WHERE schedule_type = 'weekly'").fetchone()
        self.assertTrue(weekly["next_run_at"])

        with connect() as conn:
            conn.execute("UPDATE equipment SET is_active = 0 WHERE id = 1")
        status, _, _ = self.client.request("POST", f"/jobs/{weekly['uuid']}/run")
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            inactive_queued = conn.execute("SELECT COUNT(*) FROM backup_job_runs WHERE job_id = ? AND status = 'queued'", (weekly["id"],)).fetchone()[0]
        self.assertEqual(inactive_queued, 0)

        with connect() as conn:
            conn.execute(
                "INSERT INTO users(username, full_name, password_hash, role_id, must_change_password) VALUES (?, ?, ?, (SELECT id FROM roles WHERE slug = 'read_download'), 0)",
                ("jobsviewer", "Jobs Viewer", hash_password("ViewerSenha!2026")),
            )
        viewer = WsgiClient()
        viewer.request("POST", "/login", {"username": "jobsviewer", "password": "ViewerSenha!2026"})
        status, _, body = viewer.request("GET", "/jobs")
        self.assertTrue(status.startswith("200"))
        self.assertIn("router-edge-01", body)
        status, _, body = viewer.request("GET", "/job-runs")
        self.assertTrue(status.startswith("200"))
        status, _, _ = viewer.request("POST", "/equipment/1/jobs/run-now")
        self.assertTrue(status.startswith("403"))

        with connect() as conn:
            data = stats(conn)
            actions = {row["action"] for row in conn.execute("SELECT action FROM audit_log WHERE action LIKE 'job.%'").fetchall()}
        self.assertGreaterEqual(data["total_jobs"], 3)
        self.assertIn("job.created", actions)
        self.assertIn("job.manual_queued", actions)
        self.assertIn("job.run_success", actions)
        self.assertIn("job.run_failed", actions)
        self.assertIn("job.paused", actions)
        self.assertIn("job.resumed", actions)

        status, _, body = self.client.request("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Próximas execuções", body)

    def test_ssh_credentials_permissions_and_fake_backup(self) -> None:
        self.login_ready_admin()
        status, _, body = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Editar equipamento", body)
        self.assertIn("Adicionar credencial de acesso", body)
        self.assertIn("Executar backup agora", body)
        self.assertIn("Testar conexão", body)

        status, _, _ = self.client.request("GET", "/equipment/1/edit")
        self.assertTrue(status.startswith("200"))
        status, headers, _ = self.client.request(
            "POST",
            "/equipment/1/edit",
            {
                "name": "MK-TESTE",
                "hostname": "router-edge-01",
                "ip_address": "192.0.2.10",
                "vendor_id": "4",
                "group_id": "1",
                "pop_id": "1",
                "environment_id": "1",
                "is_active": "1",
                "ssh_backup_driver": "mikrotik_routeros",
                "ssh_port": "2222",
                "ssh_custom_command": "",
                "notes": "equipamento atualizado",
            },
        )
        self.assertTrue(status.startswith("302"))
        self.assertEqual("/equipment", headers["Location"].split("?", 1)[0])
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertTrue(status.startswith("200"))
        self.assertIn("router-edge-01", body)
        with connect() as conn:
            equipment = conn.execute("SELECT name, ip_address, ssh_backup_driver, ssh_port FROM equipment WHERE id = 1").fetchone()
        self.assertEqual(equipment["name"], "router-edge-01")
        self.assertEqual(equipment["ip_address"], "192.0.2.10")
        self.assertEqual(equipment["ssh_backup_driver"], "mikrotik_routeros")
        self.assertEqual(equipment["ssh_port"], 2222)
        status, _, body = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("router-edge-01", body)
        self.assertIn("MikroTik RouterOS", body)
        self.assertIn("2222", body)

        status, _, body = self.client.request(
            "POST",
            "/equipment/1/credentials",
            {"name": "ssh principal", "username": "backup", "password": "SenhaSSH!2026", "port": "", "is_active": "1", "notes": "sem segredo"},
        )
        self.assertTrue(status.startswith("200"), body)
        self.assertIn("Credencial salva com sucesso", body)
        with connect() as conn:
            cred = conn.execute("SELECT * FROM device_credentials").fetchone()
            self.assertNotEqual(cred["password_encrypted"], "SenhaSSH!2026")
            self.assertEqual(decrypt_secret(cred["password_encrypted"]), "SenhaSSH!2026")
            self.assertEqual(cred["port"], 2222)

        status, _, body = self.client.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("ssh principal", body)
        self.assertNotIn("SenhaSSH!2026", body)
        self.assertIn('placeholder="Deixe vazio para manter a senha atual"', body)

        status, _, _ = self.client.request(
            "POST",
            f"/credentials/{cred['id']}/edit",
            {"name": "ssh editada", "username": "backup2", "password": "", "port": "2222", "is_active": "1", "notes": "edit"},
        )
        self.assertTrue(status.startswith("200"))
        with connect() as conn:
            edited = conn.execute("SELECT * FROM device_credentials WHERE id = ?", (cred["id"],)).fetchone()
            self.assertEqual(decrypt_secret(edited["password_encrypted"]), "SenhaSSH!2026")
            self.assertEqual(edited["port"], 2222)

        self.client.request(
            "POST",
            f"/credentials/{cred['id']}/edit",
            {"name": "ssh editada", "username": "backup2", "password": "NovaSenhaSSH!2026", "port": "22", "is_active": "1", "notes": "edit"},
        )
        with connect() as conn:
            changed = conn.execute("SELECT * FROM device_credentials WHERE id = ?", (cred["id"],)).fetchone()
            self.assertEqual(decrypt_secret(changed["password_encrypted"]), "NovaSenhaSSH!2026")

        with connect() as conn:
            conn.execute(
                "INSERT INTO users(username, full_name, password_hash, role_id, must_change_password) VALUES (?, ?, ?, (SELECT id FROM roles WHERE slug = 'read_download'), 0)",
                ("sshviewer", "SSH Viewer", hash_password("ViewerSenha!2026")),
            )
        viewer = WsgiClient()
        viewer.request("POST", "/login", {"username": "sshviewer", "password": "ViewerSenha!2026"})
        status, _, body = viewer.request("GET", "/equipment/1")
        self.assertTrue(status.startswith("200"))
        self.assertIn("ssh editada", body)
        self.assertIn("backup2", body)
        self.assertNotIn("NovaSenhaSSH!2026", body)
        self.assertNotIn("Editar equipamento", body)
        self.assertNotIn("Executar backup agora", body)
        self.assertNotIn("Adicionar credencial SSH", body)
        status, _, _ = viewer.request(
            "POST",
            "/equipment/1/credentials",
            {"name": "x", "username": "x", "password": "x", "port": "22"},
        )
        self.assertTrue(status.startswith("403"))
        status, _, _ = viewer.request("POST", f"/credentials/{cred['id']}/test")
        self.assertTrue(status.startswith("403"))
        status, _, _ = viewer.request("POST", "/equipment/1/ssh-test")
        self.assertTrue(status.startswith("403"))

        class FakeStream:
            def __init__(self, value: str) -> None:
                self.value = value

            def read(self) -> bytes:
                return self.value.encode("utf-8")

        class FakeClient:
            def connect(self, **kwargs) -> None:
                self.kwargs = kwargs

            def exec_command(self, command, timeout=60):
                return None, FakeStream("identity: MK-TESTE\nversion: 7.12\n"), FakeStream("")

            def close(self) -> None:
                pass

        import backup_manager.ssh as ssh_module

        original_factory = ssh_module._client_factory
        ssh_module._client_factory = FakeClient
        try:
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
        finally:
            ssh_module._client_factory = original_factory
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/equipment/1")
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertTrue(status.startswith("200"))
        self.assertIn("Conexão SSH testada com sucesso", body)
        self.assertIn("notice success", body)
        self.assertIn('data-auto-dismiss="5000"', body)

        status, headers, _ = self.client.request("POST", "/equipment/1/jobs/run-now", {"method": "ssh"})
        self.assertTrue(status.startswith("302"))
        self.assertTrue(headers["Location"].startswith("/equipment/1?run="))
        run_uuid = headers["Location"].split("run=", 1)[1]
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertTrue(status.startswith("200"))
        self.assertIn("Backup SSH enfileirado com sucesso.", body)
        self.assertIn("notice info", body)
        self.assertIn('data-auto-dismiss="6000"', body)
        self.assertIn('onclick="this.parentElement.remove()"', body)
        self.assertIn('/static/app.js?v=', body)
        js_status, _, javascript = self.client.request("GET", "/static/app.js")
        self.assertTrue(js_status.startswith("200"))
        self.assertIn('addEventListener("mouseenter", pause)', javascript)
        self.assertIn('addEventListener("focusin", pause)', javascript)
        self.assertNotIn(">Ver backup</a>", body)
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertTrue(status.startswith("200"))
        self.assertNotIn("Backup SSH enfileirado com sucesso.", body)
        status, _, body = self.client.request("GET", f"/equipment/1?notice=success&message=Mensagem+antiga&run={run_uuid}")
        self.assertTrue(status.startswith("200"))
        self.assertNotIn("Mensagem antiga", body)
        status, _, body = self.client.request("GET", f"/job-runs/{run_uuid}/status")
        queued_payload = json.loads(body)
        self.assertEqual(queued_payload["uuid"], run_uuid)
        self.assertEqual(queued_payload["status"], "queued")
        self.assertEqual(queued_payload["trigger_type"], "manual")
        self.assertEqual(queued_payload["method"], "ssh")
        self.assertEqual(queued_payload["safe_message"], "Aguardando execução pelo worker.")
        anonymous = WsgiClient()
        status, _, _ = anonymous.request("GET", f"/job-runs/{run_uuid}/status")
        self.assertTrue(status.startswith("302"))
        with connect() as conn:
            conn.execute("UPDATE backup_job_runs SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE uuid = ?", (run_uuid,))
        status, _, body = self.client.request("GET", f"/job-runs/{run_uuid}/status")
        running_payload = json.loads(body)
        self.assertEqual(running_payload["status"], "running")
        self.assertEqual(running_payload["safe_message"], "Backup SSH em execução.")
        with connect() as conn:
            conn.execute("UPDATE backup_job_runs SET status = 'queued', started_at = NULL WHERE uuid = ?", (run_uuid,))
        with connect() as conn:
            result = run_once(conn)
            run = conn.execute("SELECT * FROM backup_job_runs WHERE method = 'ssh' ORDER BY id DESC LIMIT 1").fetchone()
            backup = conn.execute("SELECT * FROM backups WHERE source_method = 'ssh'").fetchone()
        self.assertEqual(result.failed_runs, 1)
        self.assertEqual(run["uuid"], run_uuid)
        self.assertIn(run["error_code"], {"SSH_CONNECTION_FAILED", "SSH_TIMEOUT"})
        self.assertIsNone(backup)
        status, headers, body = self.client.request("GET", f"/job-runs/{run_uuid}/status")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        payload = json.loads(body)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_code"], run["error_code"])
        self.assertEqual(payload["created_backup_uuid"], "")
        self.assertEqual(payload["created_backup_name"], "")
        self.assertEqual(payload["created_backup_size"], "")
        self.assertIn("Falha ao executar backup SSH.", payload["safe_message"])
        self.assertTrue(
            "Não foi possível estabelecer conexão SSH com o equipamento" in payload["safe_message"]
            or "Tempo limite ao tentar conectar ao equipamento" in payload["safe_message"]
        )
        self.assertNotIn("Traceback", body)
        self.assertNotIn("SenhaSSH", body)
        self.assertNotIn("password", body.lower())
        self.assertNotIn("relative_path", body)
        status, _, body = self.client.request("GET", f"/equipment/1?run={run_uuid}")
        self.assertTrue(status.startswith("200"))
        self.assertNotIn("SenhaSSH", body)
        self.assertNotIn("Traceback", body)

    def test_ssh_test_expected_failures_redirect_with_friendly_messages(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver = 'mikrotik_routeros' WHERE id = 1")
            create_credential(conn, 1, 1, "ssh", "backup", "SenhaSSH!2026", 22, "")

        class RefusedClient:
            def connect(self, **kwargs) -> None:
                raise ConnectionRefusedError("connection refused")

            def close(self) -> None:
                pass

        import backup_manager.ssh as ssh_module

        original_factory = ssh_module._client_factory
        ssh_module._client_factory = RefusedClient
        try:
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
        finally:
            ssh_module._client_factory = original_factory
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/equipment/1")
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertTrue(status.startswith("200"))
        self.assertIn("A conexao SSH foi recusada pelo equipamento", body)
        self.assertIn("notice error", body)
        self.assertIn('data-auto-dismiss="8000"', body)
        self.assertIn("Editar credencial de acesso", body)
        with connect() as conn:
            cred = conn.execute("SELECT last_test_status, last_test_error FROM device_credentials WHERE equipment_id = 1").fetchone()
            self.assertEqual(cred["last_test_status"], "failed")
            self.assertIn("recusada", cred["last_test_error"])
            self.assertEqual(integrity_check(), "ok")
            audit_row = conn.execute("SELECT action, details FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(audit_row["action"], "credential.test_failed")
            self.assertIn("SSH_CONNECTION_REFUSED", audit_row["details"])

        def refused_factory():
            raise ConnectionRefusedError("connection refused before client")

        ssh_module._client_factory = refused_factory
        try:
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
        finally:
            ssh_module._client_factory = original_factory
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/equipment/1")
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertIn("A conexao SSH foi recusada pelo equipamento", body)

        original_paramiko = ssh_module.paramiko

        class NoValidConnectionsError(Exception):
            def __init__(self) -> None:
                self.errors = {("10.0.0.2", 22): ConnectionRefusedError("connection refused")}
                super().__init__("unable to connect")

        class FakeSshException:
            pass

        FakeSshException.NoValidConnectionsError = NoValidConnectionsError

        class FakeParamiko:
            pass

        FakeParamiko.NoValidConnectionsError = NoValidConnectionsError
        FakeParamiko.ssh_exception = FakeSshException

        class NoValidClient:
            def connect(self, **kwargs) -> None:
                raise NoValidConnectionsError()

            def close(self) -> None:
                pass

        ssh_module.paramiko = FakeParamiko
        ssh_module._client_factory = NoValidClient
        try:
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
        finally:
            ssh_module.paramiko = original_paramiko
            ssh_module._client_factory = original_factory
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/equipment/1")
        with connect() as conn:
            cred = conn.execute("SELECT last_test_status, last_test_error FROM device_credentials WHERE equipment_id = 1").fetchone()
            audit_row = conn.execute("SELECT action, details FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(cred["last_test_status"], "failed")
        self.assertIn("recusada", cred["last_test_error"])
        self.assertEqual(audit_row["action"], "credential.test_failed")
        self.assertIn("SSH_CONNECTION_REFUSED", audit_row["details"])
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertTrue(status.startswith("200"))
        self.assertIn("A conexao SSH foi recusada pelo equipamento", body)

        class TimeoutClient:
            def connect(self, **kwargs) -> None:
                import socket

                raise socket.timeout("timed out")

            def close(self) -> None:
                pass

        ssh_module._client_factory = TimeoutClient
        try:
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
        finally:
            ssh_module._client_factory = original_factory
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/equipment/1")
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertIn("Tempo limite ao tentar conectar ao equipamento", body)

        if ssh_module.paramiko is not None:
            class AuthClient:
                def connect(self, **kwargs) -> None:
                    raise ssh_module.paramiko.AuthenticationException("bad password")

                def close(self) -> None:
                    pass

            ssh_module._client_factory = AuthClient
            try:
                status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
            finally:
                ssh_module._client_factory = original_factory
            self.assertTrue(status.startswith("302"))
            self.assertEqual(headers["Location"], "/equipment/1")
            status, _, body = self.client.request("GET", headers["Location"])
            self.assertIn("Falha de autenticacao. Verifique usuario e senha", body)

        ssh_module._client_factory = RefusedClient
        try:
            for _ in range(3):
                status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
                self.assertTrue(status.startswith("302"))
                self.assertEqual(headers["Location"], "/equipment/1")
                status, _, body = self.client.request("GET", "/equipment/1")
                self.assertTrue(status.startswith("200"))
                self.assertIn("A conexao SSH foi recusada pelo equipamento", body)
        finally:
            ssh_module._client_factory = original_factory

        class FakeStream:
            def __init__(self, value: str) -> None:
                self.value = value

            def read(self) -> bytes:
                return self.value.encode("utf-8")

        class SuccessClient:
            def connect(self, **kwargs) -> None:
                pass

            def exec_command(self, command, timeout=60):
                return None, FakeStream("identity: MK-TESTE\nversion: 7.12\n"), FakeStream("")

            def close(self) -> None:
                pass

        ssh_module._client_factory = SuccessClient
        try:
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
        finally:
            ssh_module._client_factory = original_factory
        self.assertTrue(status.startswith("302"))
        self.assertEqual(headers["Location"], "/equipment/1")
        status, _, body = self.client.request("GET", headers["Location"])
        self.assertTrue(status.startswith("200"))
        self.assertIn("Conexão SSH testada com sucesso", body)
        with connect() as conn:
            cred = conn.execute("SELECT last_test_status, last_test_error FROM device_credentials WHERE equipment_id = 1").fetchone()
        self.assertEqual(cred["last_test_status"], "success")
        self.assertEqual(cred["last_test_error"], "")

        ssh_module._client_factory = RefusedClient
        try:
            status, headers, _ = self.client.request("POST", "/equipment/1/jobs/run-now", {"method": "ssh"})
            self.assertTrue(status.startswith("302"))
            refused_run_uuid = headers["Location"].split("run=", 1)[1]
            with connect() as conn:
                result = run_once(conn)
                refused_run = conn.execute("SELECT * FROM backup_job_runs WHERE uuid = ?", (refused_run_uuid,)).fetchone()
                backup_count = conn.execute("SELECT COUNT(*) FROM backups WHERE source_method = 'ssh'").fetchone()[0]
        finally:
            ssh_module._client_factory = original_factory
        self.assertEqual(result.failed_runs, 1)
        self.assertEqual(refused_run["status"], "failed")
        self.assertEqual(refused_run["error_code"], "SSH_CONNECTION_REFUSED")
        self.assertEqual(backup_count, 0)
        status, _, body = self.client.request("GET", f"/job-runs/{refused_run_uuid}/status")
        self.assertTrue(status.startswith("200"))
        payload = json.loads(body)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_code"], "SSH_CONNECTION_REFUSED")
        self.assertEqual(payload["safe_message"], "Falha ao executar backup SSH. A conexão SSH foi recusada pelo equipamento.")
        self.assertEqual(payload["created_backup_uuid"], "")

    def test_ssh_readiness_distinguishes_generic_custom_command_from_specific_drivers(self) -> None:
        self.login_ready_admin()

        class FakeStream:
            def __init__(self, value: str) -> None:
                self.value = value

            def read(self) -> bytes:
                return self.value.encode("utf-8")

        class FakeClient:
            commands: list[str] = []
            connections = 0

            class HuaweiChannel:
                def __init__(self, owner) -> None:
                    self.owner = owner
                    self.pending = [b"<SWITCH-TEST>\n"]

                def recv_ready(self) -> bool:
                    return bool(self.pending)

                def recv(self, size: int) -> bytes:
                    return self.pending.pop(0)

                def send(self, value: str) -> None:
                    command = value.strip()
                    self.owner.commands.append(command)
                    output = ("Huawei VRP test output with enough bytes\n"
                              if command == "display version" else
                              "Current configuration with enough bytes for validation\n")
                    self.pending.append((command + "\n" + output + "<SWITCH-TEST>\n").encode())

                def close(self) -> None:
                    pass

            def connect(self, **kwargs) -> None:
                self.__class__.connections += 1

            def exec_command(self, command, timeout=60):
                self.__class__.commands.append(command)
                return None, FakeStream("backup-manager readiness check\n"), FakeStream("")

            def invoke_shell(self, **kwargs):
                return self.HuaweiChannel(self.__class__)

            def close(self) -> None:
                pass

        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver='generic_ssh',ssh_custom_command='' WHERE id=1")
            create_credential(conn, 1, 1, "ssh", "backup", "SenhaSSH!2026", 22, "")

        import backup_manager.ssh as ssh_module

        original_factory = ssh_module._client_factory
        ssh_module._client_factory = FakeClient
        try:
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
            self.assertTrue(status.startswith("302"))
            status, _, page = self.client.request("GET", headers["Location"])
            self.assertTrue(status.startswith("200"))
            self.assertIn("Comando SSH customizado inválido ou ausente.", page)
            with connect() as conn:
                credential = conn.execute(
                    "SELECT last_test_status,last_test_error FROM device_credentials WHERE equipment_id=1"
                ).fetchone()
            self.assertEqual(credential["last_test_status"], "failed")
            self.assertEqual(credential["last_test_error"], "Comando SSH customizado inválido ou ausente.")
            self.assertEqual(FakeClient.connections, 1)
            self.assertEqual(FakeClient.commands, ["echo backup-manager-test"])

            FakeClient.commands.clear()
            with connect() as conn:
                conn.execute("UPDATE equipment SET ssh_custom_command='display version' WHERE id=1")
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
            self.assertTrue(status.startswith("302"))
            status, _, page = self.client.request("GET", headers["Location"])
            self.assertIn("Conexão SSH testada com sucesso", page)
            self.assertEqual(FakeClient.commands, ["echo backup-manager-test"])
            self.assertNotIn("display version", FakeClient.commands)

            FakeClient.commands.clear()
            with connect() as conn:
                from backup_manager.jobs import create_job, queue_run

                valid_job = create_job(conn, 1, 1, "manual", "ssh")
                queue_run(conn, valid_job["id"], "manual", 1)
                valid_result = run_once(conn)
                valid_run = conn.execute(
                    "SELECT * FROM backup_job_runs WHERE job_id=? ORDER BY id DESC LIMIT 1", (valid_job["id"],)
                ).fetchone()
            self.assertEqual(valid_result.success_runs, 1)
            self.assertEqual(valid_run["status"], "success")
            self.assertEqual(FakeClient.commands, ["display version"])

            FakeClient.commands.clear()
            with connect() as conn:
                conn.execute("UPDATE equipment SET ssh_backup_driver='huawei_vrp',ssh_custom_command='' WHERE id=1")
            status, headers, _ = self.client.request("POST", "/equipment/1/ssh-test")
            self.assertTrue(status.startswith("302"))
            status, _, page = self.client.request("GET", headers["Location"])
            self.assertIn("Conexão SSH testada com sucesso", page)
            self.assertNotIn("Comando SSH customizado", page)
            self.assertEqual(FakeClient.commands, ["screen-length 0 temporary", "display version"])

            FakeClient.commands.clear()
            with connect() as conn:
                huawei_job = create_job(conn, 1, 1, "manual", "ssh")
                queue_run(conn, huawei_job["id"], "manual", 1)
                huawei_result = run_once(conn)
                huawei_run = conn.execute(
                    "SELECT * FROM backup_job_runs WHERE job_id=? ORDER BY id DESC LIMIT 1", (huawei_job["id"],)
                ).fetchone()
            self.assertEqual(huawei_result.success_runs, 1)
            self.assertEqual(huawei_run["status"], "success")
            self.assertEqual(
                FakeClient.commands,
                ["screen-length 0 temporary", "display current-configuration"],
            )

            equipment = {"ssh_custom_command": "", "hostname": "specific-driver"}
            for driver_key in ssh_module.DRIVER_CLASSES:
                if driver_key != "generic_ssh":
                    with self.subTest(driver=driver_key):
                        ssh_module.get_driver({**equipment, "ssh_backup_driver": driver_key}).validate_backup_readiness()

            FakeClient.commands.clear()
            with connect() as conn:
                conn.execute("UPDATE equipment SET ssh_backup_driver='generic_ssh',ssh_custom_command='' WHERE id=1")
                job = create_job(conn, 1, 1, "manual", "ssh")
                queue_run(conn, job["id"], "manual", 1)
                result = run_once(conn)
                run = conn.execute(
                    "SELECT * FROM backup_job_runs WHERE job_id=? ORDER BY id DESC LIMIT 1", (job["id"],)
                ).fetchone()
            self.assertEqual(result.failed_runs, 1)
            self.assertEqual(run["error_code"], "SSH_CUSTOM_COMMAND_MISSING")
            self.assertEqual(run["error_message"], "Comando SSH customizado inválido ou ausente.")
            self.assertEqual(FakeClient.commands, [])
            status, _, body = self.client.request("GET", f"/job-runs/{run['uuid']}/status")
            payload = json.loads(body)
            self.assertEqual(
                payload["safe_message"],
                "Falha ao executar backup SSH. Comando SSH customizado inválido ou ausente.",
            )
        finally:
            ssh_module._client_factory = original_factory

    def test_ssh_worker_fake_success_and_driver_commands(self) -> None:
        self.login_ready_admin()
        config_text = "# jul/09/2026 10:00:00 by RouterOS\n/interface bridge add name=bridge-local\n/ip address add address=10.0.0.1/24 interface=bridge-local\n"

        class FakeStream:
            def __init__(self, value: str) -> None:
                self.value = value

            def read(self) -> bytes:
                return self.value.encode("utf-8")

        class FakeClient:
            commands: list[str] = []

            def connect(self, **kwargs) -> None:
                self.kwargs = kwargs

            def exec_command(self, command, timeout=60):
                self.commands.append(command)
                return None, FakeStream(config_text), FakeStream("")

            def close(self) -> None:
                pass

        with connect() as conn:
            conn.execute("UPDATE equipment SET ssh_backup_driver = 'mikrotik_routeros' WHERE id = 1")
            create_credential(conn, 1, 1, "ssh", "backup", "SenhaSSH!2026", 22, "")
            job = conn.execute("SELECT * FROM backup_jobs WHERE method = 'ssh'").fetchone()
            if not job:
                from backup_manager.jobs import create_job, queue_run

                job = create_job(conn, 1, 1, "manual", "ssh")
                queue_run(conn, job["id"], "manual", 1)

            import backup_manager.ssh as ssh_module

            original_factory = ssh_module._client_factory
            ssh_module._client_factory = FakeClient
            try:
                result = run_once(conn)
            finally:
                ssh_module._client_factory = original_factory
            run = conn.execute("SELECT * FROM backup_job_runs WHERE method = 'ssh' ORDER BY id DESC LIMIT 1").fetchone()
            backup = conn.execute("SELECT * FROM backups WHERE source_method = 'ssh'").fetchone()
            backup_count = conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0]
            audit_text = "\n".join(row["details"] for row in conn.execute("SELECT details FROM audit_log").fetchall())

        self.assertEqual(result.success_runs, 1)
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["created_backup_id"], backup["id"])
        self.assertEqual(backup_count, 1)
        self.assertIn("_mikrotik_routeros.rsc", backup["original_filename"])
        self.assertIn("/export", FakeClient.commands)
        self.assertNotIn("interface bridge add", run["safe_log"])
        self.assertNotIn("interface bridge add", audit_text)
        self.assertNotIn("SenhaSSH", audit_text)
        status, _, body = self.client.request("GET", f"/job-runs/{run['uuid']}/status")
        self.assertTrue(status.startswith("200"))
        payload = json.loads(body)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["safe_message"], "Backup SSH concluído com sucesso.")
        self.assertEqual(payload["created_backup_uuid"], backup["uuid"])
        self.assertEqual(payload["created_backup_name"], backup["original_filename"])
        self.assertTrue(payload["created_backup_size"])
        status, _, body = self.client.request("GET", f"/equipment/1?run={run['uuid']}")
        self.assertTrue(status.startswith("200"))
        self.assertIn("status-success", body)
        self.assertIn(backup["original_filename"], body)
        self.assertIn(f'href="/backups/{backup["uuid"]}"', body)
        self.assertIn(f'href="/backups/{backup["uuid"]}/download"', body)

        from backup_manager.ssh import CiscoIOSDriver, HuaweiRouterDriver, HuaweiVRPDriver, MikroTikRouterOSDriver

        with connect() as conn:
            equipment = conn.execute("SELECT * FROM equipment WHERE id = 1").fetchone()
        self.assertEqual(MikroTikRouterOSDriver(equipment).backup_commands(), ("/export",))
        self.assertEqual(HuaweiRouterDriver(equipment).backup_commands(), ("display current-configuration | no-more",))
        self.assertEqual(HuaweiVRPDriver(equipment).backup_commands(), ("screen-length 0 temporary", "display current-configuration"))
        self.assertEqual(CiscoIOSDriver(equipment).backup_commands(), ("terminal length 0", "show running-config"))

    def test_ssh_job_missing_credential_and_driver(self) -> None:
        self.login_ready_admin()
        with connect() as conn:
            from backup_manager.jobs import create_job, queue_run

            conn.execute("UPDATE equipment SET ssh_backup_driver = 'huawei_vrp' WHERE id = 1")
            job = create_job(conn, 1, 1, "manual", "ssh")
            queue_run(conn, job["id"], "manual", 1)
            run_once(conn)
            missing_credential = conn.execute("SELECT * FROM backup_job_runs WHERE job_id = ?", (job["id"],)).fetchone()
            self.assertEqual(missing_credential["error_code"], "SSH_CREDENTIAL_MISSING")

            conn.execute("UPDATE equipment SET ssh_backup_driver = '' WHERE id = 1")
            create_credential(conn, 1, 1, "ssh", "backup", "SenhaSSH!2026", 22, "")
            job2 = create_job(conn, 1, 1, "manual", "ssh")
            queue_run(conn, job2["id"], "manual", 1)
            run_once(conn)
            missing_driver = conn.execute("SELECT * FROM backup_job_runs WHERE job_id = ?", (job2["id"],)).fetchone()
            self.assertEqual(missing_driver["error_code"], "SSH_DRIVER_MISSING")


if __name__ == "__main__":
    unittest.main()
