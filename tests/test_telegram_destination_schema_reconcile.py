import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backup_manager.db import _apply_migration


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
MIGRATION_027 = MIGRATIONS / "027_telegram_destination_schema_reconcile.sql"


class TelegramDestinationSchemaReconcileTests(unittest.TestCase):
    def database(self, through="026_telegram_backup_delivery.sql"):
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "database.sqlite3"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "CREATE TABLE schema_migrations("
            "version TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            if migration.name > through:
                break
            conn.executescript(migration.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations(version) VALUES(?)",
                (migration.stem,),
            )
        conn.commit()
        return temporary, path, conn

    @staticmethod
    def trigger_names(conn):
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'telegram_destination_unique_active_name_%'"
            )
        }

    @staticmethod
    def assert_healthy(testcase, conn):
        testcase.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
        testcase.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_clean_schema_and_schema_with_historical_triggers_converge(self):
        schemas = []
        for historical in (False, True):
            temporary, _, conn = self.database()
            try:
                if historical:
                    conn.executescript(
                        "CREATE TRIGGER telegram_destination_unique_active_name_insert "
                        "BEFORE INSERT ON telegram_destinations WHEN NEW.is_active=1 "
                        "BEGIN SELECT RAISE(ABORT,'historical'); END;"
                        "CREATE TRIGGER telegram_destination_unique_active_name_update "
                        "BEFORE UPDATE ON telegram_destinations WHEN NEW.is_active=1 "
                        "BEGIN SELECT RAISE(ABORT,'historical'); END;"
                    )
                _apply_migration(conn, MIGRATION_027)
                self.assertEqual(set(), self.trigger_names(conn))
                schemas.append(
                    conn.execute(
                        "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                        "AND name LIKE 'telegram_destination_unique_active_name_%' "
                        "ORDER BY name"
                    ).fetchall()
                )
                self.assert_healthy(self, conn)
            finally:
                conn.close()
                temporary.cleanup()
        self.assertEqual([tuple(row) for row in schemas[0]], [tuple(row) for row in schemas[1]])

    def test_repeated_framework_run_is_idempotent(self):
        temporary, _, conn = self.database()
        try:
            _apply_migration(conn, MIGRATION_027)
            conn.commit()
            # O framework ignora a versão já registrada; o SQL em si também é
            # seguro se uma ferramenta de validação o executar novamente.
            conn.executescript(MIGRATION_027.read_text(encoding="utf-8"))
            applied = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=?",
                (MIGRATION_027.stem,),
            ).fetchone()[0]
            self.assertEqual(1, applied)
            self.assertEqual(0, len(self.trigger_names(conn)))
            self.assert_healthy(self, conn)
        finally:
            conn.close()
            temporary.cleanup()

    def test_duplicate_active_names_fail_transaction_without_deleting_data(self):
        temporary, _, conn = self.database()
        try:
            conn.execute(
                "INSERT INTO telegram_destinations(uuid,name,chat_id,is_active) "
                "VALUES('one',' Principal ','-1001',1)"
            )
            conn.execute(
                "INSERT INTO telegram_destinations(uuid,name,chat_id,is_active) "
                "VALUES('two','principal','-1002',1)"
            )
            conn.commit()
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "telegram_active_destination_names_must_be_unique",
            ):
                _apply_migration(conn, MIGRATION_027)
            conn.rollback()
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM telegram_destinations").fetchone()[0])
            self.assertFalse(
                conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=?",
                    (MIGRATION_027.stem,),
                ).fetchone()
            )
            self.assert_healthy(self, conn)
        finally:
            conn.close()
            temporary.cleanup()

    def test_inactive_duplicate_topic_and_backup_state_are_preserved(self):
        temporary, _, conn = self.database()
        try:
            active_id = conn.execute(
                "INSERT INTO telegram_destinations("
                "uuid,name,chat_id,is_active,default_thread_id,use_for_backup_files) "
                "VALUES('active','Destino principal','-1001',1,1411,0)"
            ).lastrowid
            inactive_id = conn.execute(
                "INSERT INTO telegram_destinations("
                "uuid,name,chat_id,is_active,default_thread_id,use_for_backup_files) "
                "VALUES('inactive','Destino principal','-1002',0,NULL,0)"
            ).lastrowid
            conn.commit()
            before = conn.execute(
                "SELECT id,name,chat_id,is_active,default_thread_id,use_for_backup_files "
                "FROM telegram_destinations ORDER BY id"
            ).fetchall()
            _apply_migration(conn, MIGRATION_027)
            after = conn.execute(
                "SELECT id,name,chat_id,is_active,default_thread_id,use_for_backup_files "
                "FROM telegram_destinations ORDER BY id"
            ).fetchall()
            self.assertEqual([tuple(row) for row in before], [tuple(row) for row in after])
            self.assertEqual((1411, 0), tuple(conn.execute(
                "SELECT default_thread_id,use_for_backup_files FROM telegram_destinations WHERE id=?",
                (active_id,),
            ).fetchone()))
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM telegram_backup_items").fetchone()[0])
            self.assertTrue(inactive_id)
            self.assert_healthy(self, conn)
        finally:
            conn.close()
            temporary.cleanup()

    def test_copy_with_historical_triggers_can_be_reconciled_twice_safely(self):
        temporary, path, conn = self.database()
        copy_dir = tempfile.TemporaryDirectory()
        try:
            conn.executescript(
                "CREATE TRIGGER telegram_destination_unique_active_name_insert "
                "BEFORE INSERT ON telegram_destinations WHEN NEW.is_active=1 "
                "BEGIN SELECT RAISE(ABORT,'historical'); END;"
                "CREATE TRIGGER telegram_destination_unique_active_name_update "
                "BEFORE UPDATE ON telegram_destinations WHEN NEW.is_active=1 "
                "BEGIN SELECT RAISE(ABORT,'historical'); END;"
            )
            conn.commit()
            conn.close()
            copied = Path(copy_dir.name) / "copy.sqlite3"
            shutil.copy2(path, copied)
            copy_conn = sqlite3.connect(copied)
            try:
                _apply_migration(copy_conn, MIGRATION_027)
                copy_conn.commit()
                self.assertEqual(0, len(self.trigger_names(copy_conn)))
                self.assert_healthy(self, copy_conn)
            finally:
                copy_conn.close()
        finally:
            temporary.cleanup()
            copy_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
