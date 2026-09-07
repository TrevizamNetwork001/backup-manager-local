from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
import unittest
from datetime import datetime, timezone

os.environ.setdefault("BACKUP_MANAGER_DATA", tempfile.mkdtemp(prefix="backup-manager-cloud-data-"))
os.environ.setdefault("BACKUP_MANAGER_DB", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "cloud.sqlite3"))
os.environ.setdefault("BACKUP_MANAGER_STORAGE_ROOT", tempfile.mkdtemp(prefix="backup-manager-cloud-storage-"))

from backup_manager.cloud_sync import (cancel_item, enqueue_backup, is_backup_cloud_eligible,
    process_item, retry_item, run_once, window_open)
from backup_manager.db import connect, migrate
from backup_manager.storage import backup_relative_path, ensure_directories, load_config


class CloudSyncTest(unittest.TestCase):
    def setUp(self):
        migrate(31)
        with connect() as conn:
            conn.execute("DELETE FROM cloud_sync_items")
            conn.execute("DELETE FROM cloud_sync_policies")
            conn.execute("DELETE FROM rclone_connections")
            conn.execute("DELETE FROM cloud_targets")
            hostname = f"cloud-{uuid.uuid4().hex[:12]}"
            conn.execute("INSERT INTO equipment(hostname,ip_address,name) VALUES(?,'192.0.2.10','Cloud Router')", (hostname,))
            self.equipment_id = conn.execute("SELECT id FROM equipment WHERE hostname=?", (hostname,)).fetchone()[0]
            target_uuid = str(uuid.uuid4())
            conn.execute("INSERT INTO cloud_targets(uuid,name,provider,mode,max_attempts,retry_base_seconds) VALUES(?,?,'simulate','simulate',3,10)",(target_uuid,"Cofre simulado"))
            self.target_id = conn.execute("SELECT id FROM cloud_targets WHERE uuid=?",(target_uuid,)).fetchone()[0]
            conn.execute("INSERT INTO cloud_sync_policies(uuid,target_id,scope_type,enabled,sync_new_backups,include_ssh,include_ftp) VALUES(?,?,'global',1,1,1,1)",(str(uuid.uuid4()),self.target_id))
            self.policy_id = conn.execute("SELECT id FROM cloud_sync_policies WHERE target_id=?",(self.target_id,)).fetchone()[0]
            config = load_config(conn); ensure_directories(config)
            content = b"hostname cloud-router\n"
            backup_uuid = str(uuid.uuid4()); relative = backup_relative_path(self.equipment_id,backup_uuid)
            path = config.backup_directory / relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content)
            conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,source_method,backup_status)
                VALUES(?,?,?,?,?,?,?,'ssh','available')""",(backup_uuid,self.equipment_id,"router.cfg","safe.backup",relative,len(content),hashlib.sha256(content).hexdigest()))
            self.backup_id = conn.execute("SELECT id FROM backups WHERE uuid=?",(backup_uuid,)).fetchone()[0]

    def test_migration_and_global_eligibility_duplicate(self):
        with connect() as conn:
            self.assertTrue(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,self.policy_id).eligible)
            self.assertEqual(len(enqueue_backup(conn,self.backup_id)),1)
            self.assertEqual(len(enqueue_backup(conn,self.backup_id)),0)
            reason=is_backup_cloud_eligible(conn,self.backup_id,self.target_id,self.policy_id).reason_code
        self.assertEqual(reason,"DUPLICATE_SYNC_ITEM")

    def test_equipment_policy_and_method_size_status_guards(self):
        with connect() as conn:
            conn.execute("DELETE FROM cloud_sync_policies")
            conn.execute("INSERT INTO cloud_sync_policies(uuid,target_id,scope_type,equipment_id,enabled,include_ssh,include_ftp) VALUES(?,?,'equipment',?,1,0,1)",(str(uuid.uuid4()),self.target_id,self.equipment_id))
            policy=conn.execute("SELECT id FROM cloud_sync_policies").fetchone()[0]
            self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,policy).reason_code,"METHOD_NOT_ALLOWED")
            conn.execute("UPDATE cloud_sync_policies SET include_ssh=1,minimum_file_size=999")
            self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,policy).reason_code,"FILE_TOO_SMALL")
            conn.execute("UPDATE cloud_sync_policies SET minimum_file_size=NULL,enabled=0")
            self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,policy).reason_code,"POLICY_DISABLED")
            conn.execute("UPDATE cloud_sync_policies SET enabled=1"); conn.execute("UPDATE cloud_targets SET is_active=0")
            self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,policy).reason_code,"TARGET_DISABLED")

    def test_missing_size_hash_trashed_deleted(self):
        with connect() as conn:
            config=load_config(conn); backup=conn.execute("SELECT * FROM backups WHERE id=?",(self.backup_id,)).fetchone(); path=config.backup_directory/backup["relative_path"]
            path.unlink(); self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,self.policy_id).reason_code,"BACKUP_FILE_MISSING")
            path.write_bytes(b"different size"); self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,self.policy_id).reason_code,"BACKUP_SIZE_MISMATCH")
            content=b"hostname cloud-router\n"; path.write_bytes(content); conn.execute("UPDATE backups SET sha256='bad' WHERE id=?",(self.backup_id,))
            self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,self.policy_id).reason_code,"BACKUP_HASH_MISMATCH")
            conn.execute("UPDATE backups SET backup_status='trashed',trash_relative_path='x' WHERE id=?",(self.backup_id,))
            self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,self.policy_id).reason_code,"BACKUP_TRASHED")
            conn.execute("UPDATE backups SET backup_status='deleted',deleted_at=CURRENT_TIMESTAMP WHERE id=?",(self.backup_id,))
            self.assertEqual(is_backup_cloud_eligible(conn,self.backup_id,self.target_id,self.policy_id).reason_code,"BACKUP_DELETED")

    def test_worker_success_failure_backoff_max_retry_cancel(self):
        with connect() as conn:
            enqueue_backup(conn,self.backup_id); item=conn.execute("SELECT id FROM cloud_sync_items").fetchone()[0]
            result=run_once(conn); self.assertEqual(result["synced"],1)
            row=conn.execute("SELECT * FROM cloud_sync_items WHERE id=?",(item,)).fetchone()
            self.assertEqual(row["status"],"synced"); self.assertTrue(row["remote_object_id"].startswith("simulate:")); self.assertEqual(row["bytes_uploaded"],row["bytes_total"])
            conn.execute("UPDATE cloud_sync_items SET status='cancelled' WHERE id=?",(item,)); enqueue_backup(conn,self.backup_id)
            item=conn.execute("SELECT id FROM cloud_sync_items WHERE status='queued'").fetchone()[0]
            self.assertEqual(process_item(conn,item,simulate_failure=True),"retry_wait")
            row=conn.execute("SELECT * FROM cloud_sync_items WHERE id=?",(item,)).fetchone(); self.assertEqual(row["attempt"],1); self.assertIsNotNone(row["next_attempt_at"])
            conn.execute("UPDATE cloud_sync_items SET status='queued',attempt=2 WHERE id=?",(item,)); self.assertEqual(process_item(conn,item,simulate_failure=True),"failed")
            self.assertTrue(retry_item(conn,item)); self.assertTrue(cancel_item(conn,item)); self.assertEqual(conn.execute("SELECT status FROM cloud_sync_items WHERE id=?",(item,)).fetchone()[0],"cancelled")

    def test_windows_and_outside_does_not_increment(self):
        self.assertTrue(window_open("08:00","18:00",datetime(2026,1,1,12,tzinfo=timezone.utc)))
        self.assertFalse(window_open("08:00","18:00",datetime(2026,1,1,20,tzinfo=timezone.utc)))
        self.assertTrue(window_open("22:00","06:00",datetime(2026,1,1,23,tzinfo=timezone.utc)))
        with connect() as conn:
            conn.execute("UPDATE cloud_targets SET sync_window_start='08:00',sync_window_end='09:00'")
            enqueue_backup(conn,self.backup_id); item=conn.execute("SELECT id FROM cloud_sync_items").fetchone()[0]
            self.assertEqual(process_item(conn,item,now=datetime(2026,1,1,12,tzinfo=timezone.utc)),"window_closed")
            self.assertEqual(conn.execute("SELECT attempt FROM cloud_sync_items WHERE id=?",(item,)).fetchone()[0],0)

    def test_worker_never_changes_local_backup(self):
        with connect() as conn:
            before=dict(conn.execute("SELECT * FROM backups WHERE id=?",(self.backup_id,)).fetchone())
            enqueue_backup(conn,self.backup_id); run_once(conn)
            after=dict(conn.execute("SELECT * FROM backups WHERE id=?",(self.backup_id,)).fetchone())
        self.assertEqual(before,after)
