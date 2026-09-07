import sqlite3
import unittest
import uuid
from pathlib import Path

from backup_manager.db import _apply_migration


ROOT = Path(__file__).resolve().parents[1]


class FTPAccountLifecycleMigrationTests(unittest.TestCase):
    def legacy_database(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        for migration in sorted((ROOT / "migrations").glob("*.sql")):
            if migration.name >= "024_ftp_account_lifecycle.sql":
                break
            conn.executescript(migration.read_text())
        equipment = conn.execute(
            "INSERT INTO equipment(hostname,ip_address,is_active) VALUES('router-legado','192.0.2.1',1)"
        ).lastrowid
        account = conn.execute(
            """INSERT INTO ftp_accounts(uuid,equipment_id,name,username,password_hash_or_secret_reference,
               home_relative_path,is_active) VALUES(?,?,?,?,?,?,1)""",
            (str(uuid.uuid4()), equipment, "Legada", "legada", "secret-ref", "legacy/home"),
        ).lastrowid
        conn.execute(
            """INSERT INTO ftp_received_files(uuid,ftp_account_id,equipment_id,original_filename,
               incoming_relative_path,status,file_size) VALUES(?,?,?,?,?,'imported',42)""",
            (str(uuid.uuid4()), account, equipment, "historico.backup", "incoming/historico.backup"),
        )
        conn.commit()
        return conn, equipment, account

    def test_upgrade_preserves_rows_history_and_foreign_keys(self):
        conn, equipment, account = self.legacy_database()
        try:
            _apply_migration(conn, ROOT / "migrations/024_ftp_account_lifecycle.sql")
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM ftp_accounts WHERE id=?", (account,)).fetchone()[0])
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM ftp_received_files WHERE ftp_account_id=?", (account,)).fetchone()[0])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
            conn.execute("UPDATE ftp_accounts SET is_active=0,deleted_at=CURRENT_TIMESTAMP WHERE id=?", (account,))
            conn.execute(
                """INSERT INTO ftp_accounts(uuid,equipment_id,name,username,password_hash_or_secret_reference,
                   home_relative_path,is_active) VALUES(?,?,?,?,?,?,1)""",
                (str(uuid.uuid4()), equipment, "Nova", "legada", "new-ref", "new/home"),
            )
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM ftp_accounts WHERE equipment_id=?", (equipment,)).fetchone()[0])
        finally:
            conn.close()

    def test_framework_execution_is_repeatable(self):
        conn, _, _ = self.legacy_database()
        try:
            path = ROOT / "migrations/024_ftp_account_lifecycle.sql"
            for _ in range(2):
                applied = conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (path.stem,)).fetchone()
                if not applied:
                    _apply_migration(conn, path)
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='024_ftp_account_lifecycle'").fetchone()[0])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
