import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backup_manager import db as database


ROOT = Path(__file__).resolve().parents[1]


def legacy_rc_database(path: Path, existing=()):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    for migration in sorted((ROOT / "migrations").glob("*.sql")):
        if migration.name >= "020_":
            continue
        sql = migration.read_text()
        if migration.name == "017_telegram_advanced.sql":
            sql = sql.replace("ALTER TABLE notification_queue ADD COLUMN destination_id INTEGER REFERENCES telegram_destinations(id);\n", "")
            sql = sql.replace("ALTER TABLE notification_queue ADD COLUMN thread_id INTEGER;\n", "")
            sql = sql.replace("CREATE INDEX idx_notification_queue_destination ON notification_queue(destination_id);\n", "")
        conn.executescript(sql)
        conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)", (migration.stem,))
    for column in existing:
        conn.execute(f"ALTER TABLE notification_queue ADD COLUMN {column} INTEGER")
    channel = conn.execute("SELECT id FROM notification_channels WHERE channel_type='telegram'").fetchone()[0]
    conn.execute("INSERT INTO notification_queue(uuid,channel_id,event_type,severity,subject,message,dedup_key) VALUES('legacy',?,'test','info','Antes','preservar','legacy')", (channel,))
    conn.commit()
    return conn


class NotificationUpgradeTests(unittest.TestCase):
    def apply(self, existing=()):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        path = root / "upgrade.sqlite3"
        conn = legacy_rc_database(path, existing)
        conn.close()
        with mock.patch.object(database, "DB_PATH", path), mock.patch.object(database, "DATA_DIR", root):
            database.migrate(31)
            database.migrate(31)
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            self.assertFalse(database.schema_problems(conn))
            self.assertEqual("preservar", conn.execute("SELECT message FROM notification_queue WHERE uuid='legacy'").fetchone()[0])
            self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(notification_queue)")}
            self.assertIn("idx_notification_queue_destination", indexes)
            self.assertIn("idx_notification_queue_thread", indexes)
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='020_fix_notification_queue_destinations'").fetchone()[0])
            conn.close()
        temporary.cleanup()

    def test_upgrade_from_rc_schema_preserves_data(self):
        self.apply()

    def test_partially_applied_destination_is_safe(self):
        self.apply(("destination_id",))

    def test_partially_applied_thread_is_safe(self):
        self.apply(("thread_id",))

    def test_failed_repair_rolls_back_column_and_version(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        path = root / "rollback.sqlite3"
        legacy_rc_database(path).close()

        def fail_after_change(conn):
            conn.execute("ALTER TABLE notification_queue ADD COLUMN destination_id INTEGER")
            raise RuntimeError("synthetic migration failure")

        with mock.patch.object(database, "DB_PATH", path), mock.patch.object(database, "DATA_DIR", root), \
             mock.patch.object(database, "_apply_notification_queue_repair", side_effect=fail_after_change):
            with self.assertRaises(RuntimeError):
                database.migrate(31)
        conn = sqlite3.connect(path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(notification_queue)")}
        self.assertNotIn("destination_id", columns)
        self.assertFalse(conn.execute("SELECT 1 FROM schema_migrations WHERE version='020_fix_notification_queue_destinations'").fetchone())
        conn.close()
        temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
