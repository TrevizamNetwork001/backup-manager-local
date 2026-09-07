from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit
from wsgiref.util import setup_testing_defaults

os.environ.setdefault("BACKUP_MANAGER_DATA", tempfile.mkdtemp(prefix="backup-manager-reports-test-"))
os.environ.setdefault("BACKUP_MANAGER_DB", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "test.sqlite3"))
os.environ.setdefault("BACKUP_MANAGER_STORAGE_ROOT", tempfile.mkdtemp(prefix="backup-manager-reports-storage-"))

from backup_manager.app import application  # noqa: E402
from backup_manager.db import DATA_DIR, TEMP_ADMIN_PASSWORD, connect, migrate  # noqa: E402
from backup_manager.reports import csv_export, parse_filters, pdf_export, xlsx_export  # noqa: E402
from backup_manager.security import hash_password  # noqa: E402


class Client:
    def __init__(self) -> None:
        self.cookie = ""

    def request(self, method: str, path: str, data: dict[str, str] | None = None) -> tuple[str, dict, bytes]:
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
            self.cookie = f'bm_session={cookie["bm_session"].value}'
        return captured["status"], captured["headers"], response


class ReportsTest(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        migrate(31)
        self.client = Client()

    def login_admin(self) -> None:
        self.client.request("POST", "/login", {"username": "admin", "password": TEMP_ADMIN_PASSWORD})
        self.client.request("POST", "/change-password", {
            "current_password": TEMP_ADMIN_PASSWORD, "new_password": "NovaSenha!2026", "confirm_password": "NovaSenha!2026",
        })

    def authenticated_role(self, *, can_download: int) -> None:
        self.client.request("GET", "/login")
        token = uuid.uuid4().hex
        with connect() as conn:
            conn.execute("INSERT INTO roles(slug,name,can_download,can_admin) VALUES(?,?,?,0)",
                         (f"test-{token}", "Perfil de teste", can_download))
            role_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO users(username,full_name,password_hash,role_id,must_change_password) VALUES(?,?,?,?,0)",
                         (f"user-{token}", "Usuário", hash_password("Senha!2026"), role_id))
            user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)", (
                token, user_id, (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            ))
        self.client.cookie = f"bm_session={token}"

    def test_csv_is_utf8_and_configurable(self) -> None:
        payload = csv_export(["Equipamento", "Situação"], [["roteador-á", "Sucesso"]], ",")
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        self.assertIn("roteador-á,Sucesso", payload.decode("utf-8-sig"))

    def test_xlsx_has_headers_widths_and_filters(self) -> None:
        payload = xlsx_export(["Equipamento", "Status"], [["router-01", "success"]])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
            styles = archive.read("xl/styles.xml").decode()
        self.assertIn("autoFilter", sheet)
        self.assertIn("customWidth", sheet)
        self.assertIn("Equipamento", sheet)
        self.assertIn("applyFill", styles)

    def test_pdf_has_professional_sections_and_empty_state(self) -> None:
        payload = pdf_export(["Data", "Equipamento"], [], title="Relatório de Backups",
                             company="Empresa Exemplo", period="01/07/2026 a 13/07/2026")
        self.assertTrue(payload.startswith(b"%PDF-1.4"))
        self.assertIn(b"Backup Manager Local", payload)
        self.assertIn(b"Empresa Exemplo", payload)
        self.assertIn(b"Nenhum registro", payload)
        self.assertIn(b"P\xe1gina 1/1", payload)

    def test_filters_are_validated(self) -> None:
        filters = parse_filters({"start": "2026-07-01", "end": "inválido", "equipment_id": "7",
                                 "status": "success", "method": "ssh", "page": "2", "per_page": "100"})
        self.assertEqual(filters.start, "2026-07-01")
        self.assertEqual(filters.end, "")
        self.assertEqual(filters.equipment_id, 7)
        self.assertEqual(filters.status, "success")
        self.assertEqual(filters.method, "ssh")
        self.assertEqual((filters.page, filters.per_page), (2, 100))

    def test_backup_filters_are_applied_to_aggregated_query(self) -> None:
        self.login_admin()
        with connect() as conn:
            conn.execute("INSERT INTO equipment(hostname,ip_address,name) VALUES('router-ok','10.0.0.1','Router OK')")
            first = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO equipment(hostname,ip_address,name) VALUES('router-fail','10.0.0.2','Router Fail')")
            second = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,
                         source_method,backup_status,received_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         (str(uuid.uuid4()), first, "ok.cfg", "ok.cfg", "backups/ok.cfg", 10, "a" * 64,
                          "ssh", "available", "2026-07-10 10:00:00"))
            conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,
                         source_method,backup_status,received_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         (str(uuid.uuid4()), second, "fail.cfg", "fail.cfg", "backups/fail.cfg", 20, "b" * 64,
                          "ftp", "failed", "2026-07-10 11:00:00"))
        path = f"/reports/backups?equipment_id={first}&status=success&method=ssh&start=2026-07-01&end=2026-07-31"
        status, _, body = self.client.request("GET", path)
        self.assertTrue(status.startswith("200"))
        self.assertIn(b">router-ok</td>", body)
        self.assertNotIn(b">router-fail</td>", body)

    def test_pagination_and_empty_reports(self) -> None:
        self.login_admin()
        with connect() as conn:
            conn.executemany("INSERT INTO equipment(hostname,ip_address,name) VALUES(?,?,?)",
                             [(f"router-{index:02d}", f"10.0.0.{index}", f"Router {index}") for index in range(1, 31)])
        status, _, body = self.client.request("GET", "/reports/equipment?per_page=25")
        self.assertTrue(status.startswith("200"))
        self.assertIn(b"P\xc3\xa1gina 1 de 2", body)
        status, _, body = self.client.request("GET", "/reports/backups")
        self.assertTrue(status.startswith("200"))
        self.assertIn(b"Nenhum registro encontrado", body)
        for path in ("/reports", "/reports/storage", "/reports/ftp", "/reports/audit", "/reports/exports"):
            status, _, body = self.client.request("GET", path)
            self.assertTrue(status.startswith("200"), path)
            self.assertIn(b"Relat\xc3\xb3rios", body)

    def test_overview_quick_period_has_apply_button_and_preserves_preset(self) -> None:
        self.login_admin()
        status, _, body = self.client.request(
            "GET", "/reports?period=10&start=2026-07-11&end=2026-07-20"
        )
        self.assertTrue(status.startswith("200"))
        page = body.decode("utf-8")
        self.assertIn('name="period"', page)
        self.assertIn('value="10" selected', page)
        self.assertIn('class="report-period-apply"', page)
        self.assertIn("Aplicar período", page)

    def test_overview_filter_handles_backup_without_chart_result(self) -> None:
        self.login_admin()
        with connect() as conn:
            conn.execute("INSERT INTO equipment(hostname,ip_address,name) VALUES('router-filter','10.0.0.9','Router Filter')")
            equipment_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,
                         source_method,backup_status,received_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                         (str(uuid.uuid4()), equipment_id, "pending.cfg", "pending.cfg", "backups/pending.cfg", 10,
                          "c" * 64, "ssh", "receiving", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        status, _, body = self.client.request("GET", f"/reports?equipment_id={equipment_id}")
        self.assertTrue(status.startswith("200"))
        self.assertIn(b"router-filter", body)

    def test_permissions_allow_consultation_and_restrict_export(self) -> None:
        self.authenticated_role(can_download=0)
        status, _, _ = self.client.request("GET", "/reports")
        self.assertTrue(status.startswith("200"))
        status, _, _ = self.client.request("GET", "/reports/export?format=csv")
        self.assertTrue(status.startswith("403"))

        self.setUp()
        self.authenticated_role(can_download=1)
        status, headers, payload = self.client.request("GET", "/reports/export?format=xlsx")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(payload.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
