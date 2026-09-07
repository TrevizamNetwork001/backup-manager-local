import hashlib
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from backup_manager.db import _apply_migration
from backup_manager.telegram_backup import enqueue_backup, enqueue_new_backup

ROOT = Path(__file__).resolve().parents[1]


class TelegramReconciliationTests(unittest.TestCase):
    def schema(self, through="999"):
        conn=sqlite3.connect(":memory:"); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        for path in sorted((ROOT/"migrations").glob("*.sql")):
            if path.name > through: break
            conn.executescript(path.read_text())
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)",(path.stem,))
        return conn

    def test_026_accepts_historical_025_columns_and_repeated_framework_run(self):
        conn=self.schema("025_telegram_destination_cleanup.sql")
        try:
            historical=ROOT/"migrations/026_telegram_backup_delivery.sql"
            for sql in ("ALTER TABLE telegram_backup_policies ADD COLUMN all_artifacts INTEGER NOT NULL DEFAULT 1", "ALTER TABLE telegram_backup_policies ADD COLUMN file_types TEXT NOT NULL DEFAULT ''", "ALTER TABLE telegram_backup_items ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''", "ALTER TABLE telegram_backup_items ADD COLUMN last_attempt_at TEXT"):
                conn.execute(sql)
            conn.commit(); _apply_migration(conn,historical)
            self.assertEqual(1,conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='026_telegram_backup_delivery'").fetchone()[0])
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
        finally: conn.close()

    def test_pipeline_policy_override_filter_idempotency_and_local_copy(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); [ (root/n).mkdir() for n in ("backups","trash","quarantine","temporary") ]
            old=os.environ.get("BACKUP_MANAGER_STORAGE_ROOT"); os.environ["BACKUP_MANAGER_STORAGE_ROOT"]=raw
            conn=self.schema()
            try:
                for key,value in {"storage_root":root,"backup_directory":root/"backups","trash_directory":root/"trash","quarantine_directory":root/"quarantine","temporary_directory":root/"temporary","telegram_backup_enabled":"1"}.items(): conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
                equipment=conn.execute("INSERT INTO equipment(hostname,ip_address,is_active) VALUES('core','192.0.2.1',1)").lastrowid
                destination=conn.execute("INSERT INTO telegram_destinations(uuid,name,chat_id,is_active,use_for_backup_files) VALUES(?,'Arquivos','-100123',1,1)",(str(uuid.uuid4()),)).lastrowid
                policy=conn.execute("INSERT INTO telegram_backup_policies(uuid,scope_type,destination_id,all_artifacts,file_types) VALUES(?,'global',?,0,'rsc')",(str(uuid.uuid4()),destination)).lastrowid
                content=b'export'; buuid=str(uuid.uuid4()); relative=f'{equipment}/{buuid}/router.rsc'; path=root/'backups'/relative; path.parent.mkdir(parents=True); path.write_bytes(content)
                backup=conn.execute("INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,source_method,backup_reason,backup_status) VALUES(?,?,?,?,?,?,?,'ftp','scheduled','available')",(buuid,equipment,'router.rsc','router.rsc',relative,len(content),hashlib.sha256(content).hexdigest())).lastrowid
                self.assertEqual(1,len(enqueue_new_backup(conn,backup))); self.assertEqual([],enqueue_backup(conn,backup)); self.assertTrue(path.exists())
                other=conn.execute("INSERT INTO telegram_backup_policies(uuid,scope_type,equipment_id,destination_id,enabled) VALUES(?,'equipment',?,?,0)",(str(uuid.uuid4()),equipment,destination)).lastrowid
                conn.execute("DELETE FROM telegram_backup_items"); self.assertEqual([],enqueue_backup(conn,backup)); self.assertTrue(policy); self.assertTrue(other)
            finally:
                conn.close()
                if old is None: os.environ.pop("BACKUP_MANAGER_STORAGE_ROOT",None)
                else: os.environ["BACKUP_MANAGER_STORAGE_ROOT"]=old


if __name__ == "__main__": unittest.main()
