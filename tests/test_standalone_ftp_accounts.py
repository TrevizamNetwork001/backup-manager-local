import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from backup_manager.db import migrate


class StandaloneFTPAccountMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_backup_accounts_and_allows_standalone_account(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            db = root / "database.sqlite3"
            lock = root / "migration.lock"
            migrate(28, _db_path=db, _data_dir=root, _lock_path=lock)
            with closing(sqlite3.connect(db)) as conn, conn:
                equipment_id = conn.execute(
                    "INSERT INTO equipment(hostname,ip_address,is_active) VALUES('legacy','192.0.2.8',1)"
                ).lastrowid
                legacy_id = conn.execute(
                    """INSERT INTO ftp_accounts(uuid,equipment_id,name,username,
                       password_hash_or_secret_reference,home_relative_path,is_active)
                       VALUES(?,?,?,?,?,?,1)""",
                    (str(uuid.uuid4()), equipment_id, "Backup legado", "legacyftp", "secret",
                     "ftp-incoming/accounts/legacy/incoming"),
                ).lastrowid
            result = migrate(29, _db_path=db, _data_dir=root, _lock_path=lock)
            self.assertEqual((29,), result.applied)
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.row_factory = sqlite3.Row
                legacy = conn.execute("SELECT * FROM ftp_accounts WHERE id=?", (legacy_id,)).fetchone()
                self.assertEqual("backup", legacy["account_type"])
                self.assertEqual(equipment_id, legacy["equipment_id"])
                conn.execute(
                    """INSERT INTO ftp_accounts(uuid,equipment_id,account_type,name,username,
                       password_hash_or_secret_reference,home_relative_path,is_active)
                       VALUES(?,NULL,'file_server',?,?,?,?,1)""",
                    (str(uuid.uuid4()), "Arquivos OLT", "oltfiles", "secret",
                     "ftp-incoming/accounts/files/incoming"),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """INSERT INTO ftp_accounts(uuid,equipment_id,account_type,name,username,
                           password_hash_or_secret_reference,home_relative_path,is_active)
                           VALUES(?,NULL,'backup',?,?,?,?,1)""",
                        (str(uuid.uuid4()), "Inválida", "invalidftp", "secret", "invalid/home"),
                    )
                self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
                self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())


if __name__ == "__main__":
    unittest.main()
