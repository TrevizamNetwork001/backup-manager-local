import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backup_manager.ftp_diagnostics import (
    check_account_directories,
    ftp_statistics,
    reconcile_accounts,
    sync_account,
    sync_all_accounts,
)


class FTPDiagnosticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE ftp_accounts(
                id INTEGER PRIMARY KEY, uuid TEXT, username TEXT, home_relative_path TEXT,
                is_active INTEGER, deleted_at TEXT, sync_status TEXT, sync_error TEXT,
                updated_at TEXT
            );
            CREATE TABLE ftp_received_files(id INTEGER PRIMARY KEY, status TEXT, detected_at TEXT);
        """)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def config(self):
        return SimpleNamespace(storage_root=self.root)

    def test_statistics_exclude_deleted_accounts_and_count_upload_history(self) -> None:
        self.conn.executemany(
            "INSERT INTO ftp_accounts VALUES(?,?,?,?,?,?,?,?,?)",
            [(1, "a", "active", "", 1, None, "pending", "", None),
             (2, "b", "inactive", "", 0, None, "pending", "", None),
             (3, "c", "deleted", "", 1, "2026-01-01", "pending", "", None)],
        )
        self.conn.executemany(
            "INSERT INTO ftp_received_files(status,detected_at) VALUES(?,?)",
            [("imported", "2026-01-01"), ("rejected", "2026-01-02"), ("failed", "2026-01-03")],
        )
        report = ftp_statistics(self.conn)
        self.assertEqual(2, report["accounts_total"])
        self.assertEqual(1, report["accounts_active"])
        self.assertEqual(3, report["uploads_detected"])
        self.assertEqual("2026-01-03", report["last_upload"])

    def test_directory_check_and_reconciliation_return_structured_results(self) -> None:
        account_uuid = "account-uuid"
        expected_home = f"ftp-incoming/accounts/{account_uuid}/incoming"
        self.conn.execute(
            "INSERT INTO ftp_accounts VALUES(1,?,?,?,?,?,?,?,?)",
            (account_uuid, "device", expected_home, 1, None, "pending", "", None),
        )
        with patch("backup_manager.ftp_diagnostics.load_config", return_value=self.config()):
            initial = check_account_directories(self.conn)
            fixed = check_account_directories(self.conn, fix=True)
            report = reconcile_accounts(self.conn, pure_users={"device"})
        self.assertEqual({"missing"}, {item["status"] for item in initial})
        self.assertEqual({"missing"}, {item["status"] for item in fixed})
        self.assertEqual([], report["accounts"][0]["issues"])
        self.assertEqual(0, report["problems"])

    def test_sync_uses_injected_helper_and_persists_canonical_status(self) -> None:
        self.conn.executemany(
            "INSERT INTO ftp_accounts VALUES(?,?,?,?,?,?,?,?,?)",
            [(1, "a", "one", "", 1, None, "pending", "", None),
             (2, "b", "two", "", 1, None, "pending", "", None)],
        )
        helper = lambda action, account_id: (account_id == 1, "checked")
        first = sync_account(self.conn, 1, helper=helper)
        report = sync_all_accounts(self.conn, helper=helper)
        self.assertTrue(first["ok"])
        self.assertEqual(1, report["failures"])
        states = dict(self.conn.execute("SELECT id,sync_status FROM ftp_accounts"))
        self.assertEqual({1: "synced", 2: "failed"}, states)


if __name__ == "__main__":
    unittest.main()
