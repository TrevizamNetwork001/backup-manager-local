from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

os.environ.setdefault("BACKUP_MANAGER_DATA", tempfile.mkdtemp(prefix="backup-manager-rclone-data-"))
os.environ.setdefault("BACKUP_MANAGER_DB", os.path.join(os.environ["BACKUP_MANAGER_DATA"], "rclone.sqlite3"))
os.environ.setdefault("BACKUP_MANAGER_STORAGE_ROOT", tempfile.mkdtemp(prefix="backup-manager-rclone-storage-"))

from backup_manager.cloud_sync import enable_automatic_backup, enqueue_backup, process_item
from backup_manager.db import connect, migrate
from backup_manager.rclone_backend import (RcloneError, RcloneUpload, begin_remote, cancel_remote, continue_remote,
                                            create_drive_remote, destination, normalize_path, normalize_remote, organized_path)
from backup_manager.storage import backup_relative_path, ensure_directories, load_config


class RcloneBackendTest(unittest.TestCase):
    def setUp(self):
        migrate(31)

    def tearDown(self):
        from backup_manager import rclone_backend
        rclone_backend._WIZARDS.clear()
        with connect() as conn:
            conn.execute("DELETE FROM cloud_sync_items")
            conn.execute("DELETE FROM cloud_sync_policies")
            conn.execute("DELETE FROM google_drive_connections")
            conn.execute("DELETE FROM rclone_connections")
            conn.execute("DELETE FROM cloud_targets")

    def test_remote_and_path_validation(self):
        self.assertEqual(normalize_remote("cofre:"), "cofre")
        self.assertEqual(normalize_path("/BackupManager/roteadores/"), "BackupManager/roteadores")
        self.assertEqual(destination("cofre", "BackupManager", "router.cfg"), "cofre:BackupManager/router.cfg")
        for invalid in ("", "a:b", "../x", "nome com espaço"):
            with self.assertRaises(ValueError): normalize_remote(invalid)
        with self.assertRaises(ValueError): normalize_path("BackupManager/../segredo")
        self.assertEqual(organized_path("BackupManager", "BGP e Borda", "Roteador São Paulo", "2026-08-03 10:00:00"),
                         "BackupManager/BGP-e-Borda/Roteador-Sao-Paulo/03-08-2026")
        self.assertEqual(organized_path("BackupManager", "", "CE-VPN", "2026-08-03"),
                         "BackupManager/SEM-GRUPO/CE-VPN/03-08-2026")
        self.assertEqual(
            organized_path("BackupManager", "Distribuicao", "CE-VPN", "2026-08-03", system_name="PEDRAS TELECOM"),
            "PEDRAS-TELECOM/CE-VPN/03-08-2026",
        )

    def test_legacy_remote_base_preserves_nested_folders(self):
        self.assertEqual(organized_path("Clientes/Minha Empresa", "Borda", "R1", "2026-09-07"),
                         "Clientes/Minha Empresa/Borda/R1/07-09-2026")
        with self.assertRaises(ValueError):
            organized_path("Clientes/../segredo", "Borda", "R1", "2026-09-07")

    def test_missing_binary_is_safe(self):
        from backup_manager import rclone_backend
        with mock.patch.object(rclone_backend.shutil, "which", return_value=None):
            with self.assertRaises(RcloneError) as error: rclone_backend.list_remotes()
        self.assertEqual(error.exception.code, "RCLONE_NOT_INSTALLED")

    def test_visual_wizard_validates_provider_and_protocol(self):
        from backup_manager import rclone_backend
        question = '{"State":"next","Option":{"Name":"region"},"Error":""}'
        with mock.patch.object(rclone_backend, "list_remotes", return_value=[]), mock.patch.object(
                rclone_backend, "_run", return_value=mock.Mock(stdout=question)) as run:
            payload = begin_remote("cofre", "s3")
        self.assertEqual(payload["State"], "next")
        self.assertIn("--non-interactive", run.call_args.args[0])
        with self.assertRaises(ValueError): begin_remote("cofre", "backend-invalido")

    def test_visual_wizard_continues_without_shell(self):
        from backup_manager import rclone_backend
        complete = '{"State":"","Option":null,"Error":"","Result":""}'
        rclone_backend._WIZARDS["cofre"] = {"provider":"s3","answers":{"access_key_id":"anterior"},"option":"region"}
        with mock.patch.object(rclone_backend, "list_remotes", return_value=["cofre"]), mock.patch.object(
                rclone_backend, "_run", return_value=mock.Mock(stdout=complete)) as run:
            payload = continue_remote("cofre", "*state,1", "resposta")
        self.assertEqual(payload["State"], "")
        self.assertEqual(run.call_args.args[0][:3], ["config", "update", "cofre"])
        self.assertIn("access_key_id", run.call_args.args[0])
        self.assertIn("region", run.call_args.args[0])
        with self.assertRaises(ValueError): continue_remote("cofre", "estado\ninvalido", "x")

    def test_drive_wizard_skips_safe_technical_options(self):
        from backup_manager import rclone_backend
        rclone_backend._WIZARDS["drive"] = {"provider":"drive","answers":{},"option":"client_secret"}
        steps = [
            {"State":"scope-state","Option":{"Name":"scope"}},
            {"State":"service-state","Option":{"Name":"service_account_file"}},
            {"State":"advanced-state","Option":{"Name":"config_fs_advanced"}},
            {"State":"local-state","Option":{"Name":"config_is_local"}},
            {"State":"token-state","Option":{"Name":"config_token","Help":"Authorize"}},
        ]
        with mock.patch.object(rclone_backend, "list_remotes", return_value=["drive"]), mock.patch.object(
                rclone_backend, "_config_protocol", side_effect=steps) as protocol:
            payload = continue_remote("drive", "secret-state", "secret")
        self.assertEqual(payload["Option"]["Name"], "config_token")
        calls = protocol.call_args_list
        self.assertIn("drive.file", calls[1].args[0])
        self.assertIn("false", calls[3].args[0])
        self.assertIn("false", calls[4].args[0])

    def test_visual_wizard_cancel_removes_only_active_incomplete_remote(self):
        from backup_manager import rclone_backend
        rclone_backend._WIZARDS["cofre"] = {"provider":"s3","answers":{},"option":"region"}
        with mock.patch.object(rclone_backend, "list_remotes", return_value=["cofre"]), mock.patch.object(
                rclone_backend, "_run", return_value=mock.Mock()) as run:
            self.assertTrue(cancel_remote("cofre"))
        run.assert_called_once_with(["config", "delete", "cofre"], timeout=20)
        self.assertNotIn("cofre", rclone_backend._WIZARDS)

        with mock.patch.object(rclone_backend, "list_remotes") as remotes, mock.patch.object(
                rclone_backend, "_run") as run:
            self.assertFalse(cancel_remote("concluido"))
        remotes.assert_not_called()
        run.assert_not_called()

    def test_google_reauthorization_replaces_only_its_owned_remote(self):
        from backup_manager import rclone_backend
        with mock.patch.object(rclone_backend, "list_remotes", return_value=["meudrive", "arquivo"]), \
             mock.patch.object(rclone_backend, "_run", return_value=mock.Mock()) as run:
            create_drive_remote("meudrive", "client", "secret", "token", replace=True)
        self.assertEqual(run.call_args_list[0], mock.call(["config", "delete", "meudrive"], timeout=20))
        self.assertEqual(run.call_args_list[1].args[0][:4], ["config", "create", "meudrive", "drive"])
        self.assertNotIn("arquivo", run.call_args_list[0].args[0])

    def test_worker_uploads_through_rclone_and_preserves_local_backup(self):
        migrate(31)
        with connect() as conn:
            hostname = f"rclone-{uuid.uuid4().hex[:10]}"
            conn.execute("INSERT INTO equipment(hostname,ip_address,name) VALUES(?,'192.0.2.50','Rclone Router')", (hostname,))
            equipment_id = conn.execute("SELECT id FROM equipment WHERE hostname=?", (hostname,)).fetchone()[0]
            target_uuid = str(uuid.uuid4())
            cursor = conn.execute("INSERT INTO cloud_targets(uuid,name,provider,mode) VALUES(?,?,'simulate','simulate')", (target_uuid,"Cofre rclone"))
            target_id = cursor.lastrowid
            conn.execute("INSERT INTO rclone_connections(target_id,remote_name,base_path,status) VALUES(?,?,?,'verified')", (target_id,"cofre","BackupManager"))
            conn.execute("INSERT INTO cloud_sync_policies(uuid,target_id,scope_type,enabled) VALUES(?,?,'global',1)", (str(uuid.uuid4()),target_id))
            config = load_config(conn); ensure_directories(config)
            content = b"/export\n"; backup_uuid = str(uuid.uuid4()); relative = backup_relative_path(equipment_id,backup_uuid)
            path = config.backup_directory / relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content)
            conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,source_method,backup_status)
                VALUES(?,?,?,?,?,?,?,'ssh','available')""",(backup_uuid,equipment_id,"router.rsc","safe.rsc",relative,len(content),hashlib.sha256(content).hexdigest()))
            backup_id = conn.execute("SELECT id FROM backups WHERE uuid=?",(backup_uuid,)).fetchone()[0]
            before = dict(conn.execute("SELECT * FROM backups WHERE id=?",(backup_id,)).fetchone())
            enqueue_backup(conn,backup_id); item_id=conn.execute("SELECT id FROM cloud_sync_items WHERE backup_id=?",(backup_id,)).fetchone()[0]
            with mock.patch("backup_manager.cloud_sync.rclone_upload", return_value=RcloneUpload("cofre:BackupManager/router.rsc")) as upload:
                self.assertEqual(process_item(conn,item_id),"synced")
            after = dict(conn.execute("SELECT * FROM backups WHERE id=?",(backup_id,)).fetchone())
            item = conn.execute("SELECT status,remote_object_id,bytes_uploaded FROM cloud_sync_items WHERE id=?",(item_id,)).fetchone()
            from backup_manager.notifications import _rclone_notification
            audit_row = conn.execute("SELECT * FROM audit_log WHERE action='cloud.rclone_upload_success' ORDER BY id DESC LIMIT 1").fetchone()
            channel = conn.execute("SELECT * FROM notification_channels WHERE channel_type='telegram'").fetchone()
            subject, message, _ = _rclone_notification(conn, audit_row, {}, channel)
        self.assertEqual(before,after)
        self.assertEqual((item["status"],item["remote_object_id"],item["bytes_uploaded"]),("synced","cofre:BackupManager/router.rsc",len(content)))
        self.assertEqual(subject, "Cópia externa concluída")
        self.assertIn("Equipamento: Rclone Router", message)
        self.assertIn("Destino: Cofre rclone", message)
        self.assertIn("Pasta: cofre:BackupManager", message)
        upload.assert_called_once()

    def test_verified_google_target_enables_new_ssh_and_ftp_backups(self):
        migrate(31)
        with connect() as conn:
            cursor = conn.execute("""INSERT INTO cloud_targets(uuid,name,provider,mode,is_active)
                VALUES(?,?,'simulate','simulate',1)""", (str(uuid.uuid4()), "Google Drive"))
            target_id = cursor.lastrowid
            enable_automatic_backup(conn, target_id)
            policy = conn.execute("""SELECT enabled,sync_new_backups,include_ssh,include_ftp
                FROM cloud_sync_policies WHERE target_id=?""", (target_id,)).fetchone()
            settings = dict(conn.execute("""SELECT key,value FROM settings
                WHERE key IN ('cloud_sync_enabled','cloud_sync_auto_enqueue')"""))
            enable_automatic_backup(conn, target_id)
            count = conn.execute("SELECT COUNT(*) FROM cloud_sync_policies WHERE target_id=?", (target_id,)).fetchone()[0]
        self.assertEqual(tuple(policy), (1, 1, 1, 1))
        self.assertEqual(settings, {"cloud_sync_auto_enqueue": "1", "cloud_sync_enabled": "1"})
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
