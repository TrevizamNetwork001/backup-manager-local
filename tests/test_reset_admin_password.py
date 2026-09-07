from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from backup_manager.admin_password_reset import (
    AdminPasswordResetError,
    CONFIRMATION,
    inspect_database,
    generate_password,
    prompt_password,
    reset_admin_password,
    temp_password_message,
)
from backup_manager.db import migrate
from backup_manager.security import hash_password, verify_password


class AdminPasswordResetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="bm-admin-reset-")
        self.root = Path(self.temp.name)
        self.db = self.root / "data" / "test.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_database(self, *, historical: bool = True) -> None:
        migrate(31, _db_path=self.db, _data_dir=self.root / "data", _lock_path=self.root / "data" / "lock")
        with closing(sqlite3.connect(self.db)) as conn, conn:
            if historical:
                conn.execute(
                    "UPDATE schema_migrations SET applied_at=datetime('2020-01-01','+' || rowid || ' days')"
                )
                for index in range(3):
                    conn.execute(
                        "INSERT INTO equipment(hostname,ip_address,is_active) VALUES(?,?,1)",
                        (f"router-{index}", f"192.0.2.{index + 1}"),
                    )
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('setup_complete','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )

    def admin(self, conn):
        return conn.execute(
            "SELECT id,username,password_hash,must_change_password FROM users WHERE username='admin'"
        ).fetchone()

    def test_missing_empty_and_directory_are_refused_without_creation(self) -> None:
        for path, code in ((self.root / "missing.sqlite3", "DATABASE_MISSING"),
                           (self.root, "DATABASE_NOT_FILE")):
            with self.subTest(code=code), self.assertRaises(AdminPasswordResetError) as caught:
                inspect_database(path)
            self.assertEqual(caught.exception.code, code)
        empty = self.root / "empty.sqlite3"
        empty.touch()
        with self.assertRaises(AdminPasswordResetError) as caught:
            inspect_database(empty)
        self.assertEqual(caught.exception.code, "DATABASE_EMPTY")
        malformed = self.root / "missing-tables.sqlite3"
        with closing(sqlite3.connect(malformed)) as conn, conn:
            conn.execute("CREATE TABLE harmless(id INTEGER)")
        with self.assertRaises(AdminPasswordResetError) as caught:
            inspect_database(malformed)
        self.assertEqual(caught.exception.code, "SCHEMA_INVALID")

    def test_new_or_seed_only_database_is_suspicious(self) -> None:
        self.create_database(historical=False)
        with self.assertRaises(AdminPasswordResetError) as caught:
            inspect_database(self.db)
        self.assertEqual(caught.exception.code, "DATABASE_SUSPICIOUS")
        self.assertIn("Banco suspeito ou recém-criado", str(caught.exception))

    def test_integrity_failure_is_sanitized(self) -> None:
        self.create_database()
        data = bytearray(self.db.read_bytes())
        data[:16] = b"corrupted-sqlite!"
        self.db.write_bytes(data)
        with self.assertRaises(AdminPasswordResetError) as caught:
            inspect_database(self.db)
        self.assertIn(caught.exception.code, {"INTEGRITY_FAILED", "SCHEMA_INVALID"})
        self.assertNotIn(str(self.db), str(caught.exception))

    def test_historical_database_dry_run_has_no_writes(self) -> None:
        self.create_database()
        before = self.db.read_bytes()
        result = reset_admin_password(self.db)
        self.assertTrue(result.dry_run)
        self.assertIsNone(result.backup_path)
        self.assertEqual(before, self.db.read_bytes())

    def test_schema_incompatible_and_missing_admin_are_blocked(self) -> None:
        self.create_database()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("DELETE FROM schema_migrations WHERE version='031_rclone_external_storage'")
        with self.assertRaises(AdminPasswordResetError) as caught:
            inspect_database(self.db)
        self.assertEqual(caught.exception.code, "SCHEMA_OUTDATED")

        self.db.unlink()
        self.create_database()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE roles SET can_admin=0")
        with self.assertRaises(AdminPasswordResetError) as caught:
            reset_admin_password(self.db)
        self.assertEqual(caught.exception.code, "ADMIN_NOT_FOUND")

    def test_multiple_admins_require_explicit_selection(self) -> None:
        self.create_database()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            role = conn.execute("SELECT id FROM roles WHERE can_admin=1").fetchone()[0]
            conn.execute(
                "INSERT INTO users(username,full_name,password_hash,role_id) VALUES('second','Second',?,?)",
                (hash_password("SecondAdmin!2026"), role),
            )
        with self.assertRaises(AdminPasswordResetError) as caught:
            reset_admin_password(self.db)
        self.assertEqual(caught.exception.code, "ADMIN_SELECTION_REQUIRED")
        selected = reset_admin_password(self.db, username="second")
        self.assertEqual(selected.username, "second")

    def test_password_mismatch_is_rejected(self) -> None:
        with mock.patch("getpass.getpass", side_effect=["StrongPassword!1", "DifferentPassword!2"]):
            with self.assertRaises(AdminPasswordResetError) as caught:
                prompt_password()
        self.assertEqual(caught.exception.code, "PASSWORD_MISMATCH")
        generated = generate_password()
        self.assertGreaterEqual(len(generated), 12)

    def test_confirmed_reset_backup_sessions_counts_and_audit(self) -> None:
        self.create_database()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.row_factory = sqlite3.Row
            admin = self.admin(conn)
            role = conn.execute("SELECT id FROM roles WHERE slug='read_download'").fetchone()[0]
            other = conn.execute(
                "INSERT INTO users(username,full_name,password_hash,role_id,must_change_password) "
                "VALUES('reader','Reader',?,?,0)", (hash_password("ReaderPassword!1"), role),
            ).lastrowid
            conn.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES('admin-session',?,datetime('now','+1 day'))", (admin["id"],))
            conn.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES('reader-session',?,datetime('now','+1 day'))", (other,))
            old_hash = admin["password_hash"]
            operational = tuple(conn.execute(
                "SELECT (SELECT COUNT(*) FROM equipment),(SELECT COUNT(*) FROM backup_jobs),"
                "(SELECT COUNT(*) FROM backups),(SELECT COUNT(*) FROM ftp_accounts),"
                "(SELECT COUNT(*) FROM telegram_destinations)"
            ).fetchone())
            protected_tables = {
                row[0]: conn.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "AND name NOT IN ('users','sessions','audit_log')"
                )
            }
        result = reset_admin_password(
            self.db, confirmation=CONFIRMATION, username="admin",
            password_reader=lambda: "NewSecurePassword!2026",
        )
        self.assertFalse(result.dry_run)
        self.assertEqual(result.sessions_invalidated, 1)
        self.assertTrue(result.backup_path.is_file())
        self.assertEqual(result.backup_path.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(result.backup_path)) as backup, backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertFalse(backup.execute("PRAGMA foreign_key_check").fetchall())
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.row_factory = sqlite3.Row
            admin = self.admin(conn)
            self.assertNotEqual(admin["password_hash"], old_hash)
            self.assertTrue(verify_password("NewSecurePassword!2026", admin["password_hash"]))
            self.assertEqual(admin["must_change_password"], 1)
            self.assertFalse(conn.execute("SELECT 1 FROM sessions WHERE token='admin-session'").fetchone())
            self.assertTrue(conn.execute("SELECT 1 FROM sessions WHERE token='reader-session'").fetchone())
            after = tuple(conn.execute(
                "SELECT (SELECT COUNT(*) FROM equipment),(SELECT COUNT(*) FROM backup_jobs),"
                "(SELECT COUNT(*) FROM backups),(SELECT COUNT(*) FROM ftp_accounts),"
                "(SELECT COUNT(*) FROM telegram_destinations)"
            ).fetchone())
            self.assertEqual(operational, after)
            self.assertEqual(protected_tables, {
                table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in protected_tables
            })
            audit = conn.execute(
                "SELECT action,details FROM audit_log WHERE action='auth.admin_password_reset'"
            ).fetchone()
            self.assertEqual(audit["action"], "auth.admin_password_reset")
            details = json.loads(audit["details"])
            self.assertEqual(details["origin"], "cli")
            self.assertNotIn("NewSecurePassword", audit["details"])
            self.assertNotIn(admin["password_hash"], audit["details"])

    def test_failure_rolls_back_user_sessions_and_audit(self) -> None:
        self.create_database()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            admin = self.admin(conn)
            conn.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES('keep',?,datetime('now','+1 day'))", (admin[0],))
            before = tuple(admin)
        with mock.patch("backup_manager.admin_password_reset.hash_password", side_effect=RuntimeError("forced")):
            with self.assertRaises(RuntimeError):
                reset_admin_password(
                    self.db, confirmation=CONFIRMATION,
                    password_reader=lambda: "NewSecurePassword!2026",
                )
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(tuple(self.admin(conn)), before)
            self.assertTrue(conn.execute("SELECT 1 FROM sessions WHERE token='keep'").fetchone())
            self.assertFalse(conn.execute("SELECT 1 FROM audit_log WHERE action='auth.admin_password_reset'").fetchone())

    def test_temp_password_is_hidden_after_setup(self) -> None:
        self.create_database()
        reveal, message = temp_password_message(self.db)
        self.assertFalse(reveal)
        self.assertIn("não redefine a senha", message)
        self.assertIn("reset-admin-password", message)


if __name__ == "__main__":
    unittest.main()
