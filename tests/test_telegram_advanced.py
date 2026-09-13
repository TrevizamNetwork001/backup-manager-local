import hashlib
import os
import sqlite3
import tempfile
import unittest
import uuid
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from backup_manager.security import encrypt_secret
from backup_manager.telegram_backup import (TelegramBackupError, create_destination, enqueue_backup, is_backup_eligible,
    process_item, resolve_thread, stats)
from backup_manager.telegram_summaries import (_state, render_daily_telegram_summary, render_executive_telegram_summary,
    render_weekly_telegram_summary, schedule_summary_runs, schedule_summary_test, split_telegram_message, summary_period)
from backup_manager.telegram_formatting import pt_size, status_label, tg_escape
from backup_manager.notifications import dispatch as notification_dispatch, enqueue as enqueue_notification, queue_basic_test


ROOT = Path(__file__).resolve().parents[1]


class FakeDocumentTransport:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def send_document(self, token, chat_id, thread_id, path, filename, caption, base_url):
        self.calls.append((token, chat_id, thread_id, path, filename, caption, base_url))
        if self.error:
            raise self.error
        return "321", path.stat().st_size


class TelegramAdvancedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.storage = root / "storage"
        for name in ("backups", "trash", "quarantine", "temporary"):
            (self.storage / name).mkdir(parents=True)
        self.key = root / "secret.key"
        self.key.write_bytes(Fernet.generate_key() + b"\n"); self.key.chmod(0o640)
        self.previous_key = os.environ.get("BACKUP_MANAGER_SECRET_KEY")
        self.previous_storage = os.environ.get("BACKUP_MANAGER_STORAGE_ROOT")
        os.environ["BACKUP_MANAGER_SECRET_KEY"] = str(self.key)
        os.environ["BACKUP_MANAGER_STORAGE_ROOT"] = str(self.storage)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        for migration in sorted((ROOT / "migrations").glob("*.sql")):
            self.conn.executescript(migration.read_text())
        values = {"storage_root": self.storage, "backup_directory": self.storage / "backups",
                  "trash_directory": self.storage / "trash", "quarantine_directory": self.storage / "quarantine",
                  "temporary_directory": self.storage / "temporary", "timezone": "America/Sao_Paulo",
                  "telegram_backup_enabled": "1", "telegram_public_max_file_bytes": "52428800"}
        for key, value in values.items():
            self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        group = self.conn.execute("INSERT INTO equipment_groups(name) VALUES('OLTs')").lastrowid
        self.equipment = self.conn.execute("INSERT INTO equipment(hostname,ip_address,group_id,is_active) VALUES('OLT Centro','192.0.2.10',?,1)", (group,)).lastrowid
        self.group = group
        self.destination = self.conn.execute("""INSERT INTO telegram_destinations(uuid,name,chat_id,destination_type,is_active,use_for_alerts,use_for_daily_summary,use_for_weekly_summary,use_for_executive_summary,use_for_backup_files)
            VALUES(?, 'Laboratório','-1001234567890','supergroup',1,1,1,1,1,1)""", (str(uuid.uuid4()),)).lastrowid
        self.policy = self.conn.execute("""INSERT INTO telegram_backup_policies(uuid,scope_type,equipment_id,destination_id,send_new_backups,compression_mode)
            VALUES(?,'equipment',?,?,1,'none')""", (str(uuid.uuid4()), self.equipment, self.destination)).lastrowid
        synthetic_token = "123456:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
        self.conn.execute("UPDATE notification_channels SET is_enabled=1,token_encrypted=?,chat_id='-1001234567890' WHERE channel_type='telegram'", (encrypt_secret(synthetic_token),))

    def tearDown(self):
        self.conn.close(); self.temp.cleanup()
        if self.previous_key is None: os.environ.pop("BACKUP_MANAGER_SECRET_KEY", None)
        else: os.environ["BACKUP_MANAGER_SECRET_KEY"] = self.previous_key
        if self.previous_storage is None: os.environ.pop("BACKUP_MANAGER_STORAGE_ROOT", None)
        else: os.environ["BACKUP_MANAGER_STORAGE_ROOT"] = self.previous_storage

    def backup(self, content=b"configuration\n", name="config.txt"):
        backup_uuid = str(uuid.uuid4()); relative = f"{self.equipment}/{backup_uuid}/{name}"
        target = self.storage / "backups" / relative; target.parent.mkdir(parents=True); target.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        backup_id = self.conn.execute("""INSERT INTO backups(uuid,equipment_id,original_filename,stored_filename,relative_path,file_size,sha256,source_method,backup_reason,backup_status)
            VALUES(?,?,?,?,?,?,?,'ssh','scheduled','available')""", (backup_uuid, self.equipment, name, name, relative, len(content), digest)).lastrowid
        return backup_id, target

    def test_daily_period_is_previous_complete_local_day(self):
        period = summary_period(self.conn, "daily", datetime(2026, 7, 13, 15, tzinfo=timezone.utc))
        self.assertEqual("12/07/2026", period.label)
        self.assertEqual(3, period.start_utc.hour)  # meia-noite em São Paulo

    def test_summary_idempotency_and_executive_empty(self):
        now = datetime(2026, 7, 13, 15, tzinfo=timezone.utc)
        self.assertEqual("", render_executive_telegram_summary(self.conn, summary_period(self.conn, "executive", now)))
        self.assertEqual(1, schedule_summary_runs(self.conn, now, force_type="daily"))
        self.assertEqual(0, schedule_summary_runs(self.conn, now, force_type="daily"))

    def test_long_message_is_numbered_without_loss(self):
        message = "\n\n".join("seção " + str(index) + " " + "x" * 100 for index in range(100))
        parts = split_telegram_message(message, 500)
        self.assertGreater(len(parts), 1)
        self.assertIn(f"Parte 1 de {len(parts)}", parts[0])
        self.assertTrue(all(len(part) <= 500 for part in parts))

    def summary_data(self, **changes):
        data = {"backups": 0, "failed": 0, "pending": 0, "cancelled": 0, "by_method": {},
                "equipment_total": 1, "equipment_with": 1, "equipment_without": 0, "equipment_inactive": 0,
                "ftp": {}, "cloud": (0, 0, 0), "telegram": (0, 0, 0, 0), "backup_bytes": 0,
                "trash_bytes": 0, "period_bytes": 0, "disk_percent": 5.7, "disk_free": 1024 ** 3,
                "restored": 0, "cleanups": 0, "recurrent": [], "admin": {}}
        data.update(changes); return data

    def test_general_state_green_yellow_and_red(self):
        self.assertEqual("healthy", _state(self.summary_data())[0])
        self.assertEqual("warning", _state(self.summary_data(equipment_without=1, equipment_with=0))[0])
        self.assertEqual("critical", _state(self.summary_data(failed=2))[0])
        self.assertEqual("critical", _state(self.summary_data(disk_percent=94))[0])

    def test_daily_template_empty_failure_integrations_storage_and_html_escape(self):
        period = summary_period(self.conn, "daily", datetime(2026, 7, 13, 15, tzinfo=timezone.utc))
        data = self.summary_data(backups=5, failed=1, equipment_without=1, equipment_with=0,
                                 by_method={"ssh": 3, "ftp": 2}, ftp={"imported": 2, "rejected": 1},
                                 cloud=(5, 1, 2), telegram=(3, 0, 1, 1), backup_bytes=4300000,
                                 recurrent=[("OLT <Centro> & Norte", 2)], admin={"job.updated": 1})
        with mock.patch("backup_manager.telegram_summaries.collect_summary_data", return_value=data):
            text = render_daily_telegram_summary(self.conn, period)
        for expected in ("📦 <b>Backup Manager Local</b>", "🔴 Sistema em estado crítico", "📡 <b>Equipamentos</b>",
                         "💾 <b>Backups</b>", "📥 <b>Recebimento por FTP</b>", "☁️ <b>Sincronização externa</b>",
                         "🗄️ <b>Armazenamento</b>", "5,7%", "4,1 MB", "OLT &lt;Centro&gt; &amp; Norte"):
            self.assertIn(expected, text)
        self.assertNotIn("OLT <Centro>", text)

    def test_weekly_template_and_portuguese_catalog(self):
        period = summary_period(self.conn, "weekly", datetime(2026, 7, 13, 15, tzinfo=timezone.utc))
        with mock.patch("backup_manager.telegram_summaries.collect_summary_data", return_value=self.summary_data(backups=35, failed=1, period_bytes=1800000000)):
            text = render_weekly_telegram_summary(self.conn, period)
        self.assertIn("📊 <b>Resumo semanal</b>", text); self.assertIn("97,2%", text)
        self.assertEqual("1,0 GB", pt_size(1024 ** 3)); self.assertEqual("Aguardando envio", status_label("queued"))
        self.assertEqual("OLT &lt;1&gt;", tg_escape("OLT <1>"))

    def test_summary_test_does_not_consume_real_idempotency(self):
        now = datetime(2026, 7, 13, 15, tzinfo=timezone.utc)
        self.assertEqual(1, schedule_summary_test(self.conn, "daily", now))
        self.assertEqual(1, schedule_summary_runs(self.conn, now, force_type="daily"))
        keys = [row[0] for row in self.conn.execute("SELECT logical_key FROM telegram_summary_runs ORDER BY id")]
        self.assertTrue(any(key.startswith("test:daily:") for key in keys))
        self.assertTrue(any(not key.startswith("test:") for key in keys))

    def test_topic_resolution_equipment_precedes_group_and_default(self):
        self.conn.execute("UPDATE telegram_destinations SET default_thread_id=9 WHERE id=?", (self.destination,))
        self.conn.execute("INSERT INTO telegram_topic_mappings(uuid,destination_id,mapping_type,group_id,thread_id) VALUES(?,?,'group',?,20)", (str(uuid.uuid4()), self.destination, self.group))
        self.conn.execute("INSERT INTO telegram_topic_mappings(uuid,destination_id,mapping_type,equipment_id,thread_id) VALUES(?,?,'equipment',?,30)", (str(uuid.uuid4()), self.destination, self.equipment))
        self.assertEqual(30, resolve_thread(self.conn, self.destination, self.equipment, "ssh"))

    def test_invalid_default_topic_is_rejected_and_simple_destination_has_no_topic(self):
        with self.assertRaisesRegex(ValueError, "Tópico inválido"):
            create_destination(self.conn, name="Inválido", chat_id="-1001234567890", destination_type="supergroup", default_thread_id=0)
        backup_id, _ = self.backup()
        self.assertEqual(1, len(enqueue_backup(self.conn, backup_id)))
        item = self.conn.execute("SELECT id FROM telegram_backup_items ORDER BY id DESC LIMIT 1").fetchone()[0]
        transport = FakeDocumentTransport()
        self.assertEqual("sent", process_item(self.conn, item, transport=transport))
        self.assertIsNone(transport.calls[-1][2])

    def test_default_topic_resolves_for_general_and_equipment_notifications(self):
        self.conn.execute("UPDATE telegram_destinations SET default_thread_id=1411 WHERE id=?", (self.destination,))
        self.assertEqual(1411, resolve_thread(self.conn, self.destination, None, "", "alerts"))
        queue_uuid = enqueue_notification(self.conn, event_type="job.run_failed", severity="critical", subject="Falha", message="Falha geral", dedup_key="test-default-topic")
        queued = self.conn.execute("SELECT destination_id,thread_id FROM notification_queue WHERE uuid=?", (queue_uuid,)).fetchone()
        self.assertEqual(self.destination, queued["destination_id"])
        self.assertEqual(1411, queued["thread_id"])

    def test_basic_test_and_dispatch_preserve_supergroup_topic(self):
        self.conn.execute("UPDATE telegram_destinations SET default_thread_id=1411 WHERE id=?", (self.destination,))
        queue_uuid = queue_basic_test(self.conn)
        queued = self.conn.execute("SELECT destination_id,thread_id FROM notification_queue WHERE uuid=?", (queue_uuid,)).fetchone()
        self.assertEqual(self.destination, queued["destination_id"])
        self.assertEqual(1411, queued["thread_id"])
        class Sender:
            def __init__(self): self.calls=[]
            def send(self, token, chat_id, message, thread_id=None, parse_mode=None):
                self.calls.append((chat_id, message, thread_id, parse_mode)); return 200, "1"
        sender = Sender()
        self.assertEqual((1,0,0), notification_dispatch(self.conn, transport=sender))
        self.assertEqual(("-1001234567890", 1411, "HTML"), (sender.calls[0][0], sender.calls[0][2], sender.calls[0][3]))

    def test_eligible_duplicate_and_local_storage_preserved(self):
        backup_id, target = self.backup()
        self.assertTrue(is_backup_eligible(self.conn, backup_id, self.destination, self.policy).eligible)
        created = enqueue_backup(self.conn, backup_id)
        self.assertEqual(1, len(created)); self.assertEqual([], enqueue_backup(self.conn, backup_id))
        item = self.conn.execute("SELECT id FROM telegram_backup_items").fetchone()[0]
        transport = FakeDocumentTransport()
        self.assertEqual("sent", process_item(self.conn, item, transport=transport))
        self.assertTrue(target.is_file()); self.assertEqual(b"configuration\n", target.read_bytes())
        row = self.conn.execute("SELECT status,telegram_message_id FROM telegram_backup_items WHERE id=?", (item,)).fetchone()
        self.assertEqual(("sent", "321"), tuple(row))

    def test_ftp_storage_normalizes_permissions_for_backup_workers(self):
        from backup_manager.ftp_storage import store_backup
        source = self.storage / "temporary" / "ftp-upload.cfg"
        content = b"configuration\n"
        source.write_bytes(content)
        source.chmod(0o300)
        stored = store_backup(self.conn, equipment_id=self.equipment, source=source,
                              original_filename="ftp-upload.cfg", file_size=len(content),
                              digest=hashlib.sha256(content).hexdigest(),
                              received_at=datetime.now(timezone.utc), notes="")
        target = self.storage / "backups" / stored.relative_path
        self.assertEqual(0o640, target.stat().st_mode & 0o777)
        self.assertEqual((self.storage / "backups").stat().st_gid, target.stat().st_gid)
        self.assertEqual(0o750, target.parent.stat().st_mode & 0o777)
        self.assertEqual(content, target.read_bytes())

    def test_unreadable_backup_does_not_crash_queue(self):
        backup_id, target = self.backup()
        enqueue_backup(self.conn, backup_id)
        item = self.conn.execute("SELECT id FROM telegram_backup_items").fetchone()[0]
        with mock.patch("backup_manager.telegram_backup.sha256_file", side_effect=PermissionError):
            self.assertEqual("skipped", process_item(self.conn, item, transport=FakeDocumentTransport()))
        row = self.conn.execute("SELECT error_code FROM telegram_backup_items WHERE id=?", (item,)).fetchone()
        self.assertEqual("BACKUP_FILE_UNREADABLE", row[0])
        self.assertTrue(target.is_file())

    def test_size_limit_skips_without_splitting_or_changing_backup(self):
        backup_id, target = self.backup(b"0123456789")
        self.conn.execute("UPDATE settings SET value='5' WHERE key='telegram_public_max_file_bytes'")
        enqueue_backup(self.conn, backup_id); item = self.conn.execute("SELECT id FROM telegram_backup_items").fetchone()[0]
        self.assertEqual("skipped", process_item(self.conn, item, transport=FakeDocumentTransport()))
        row = self.conn.execute("SELECT status,error_code FROM telegram_backup_items WHERE id=?", (item,)).fetchone()
        self.assertEqual(("skipped", "TELEGRAM_FILE_TOO_LARGE"), tuple(row)); self.assertTrue(target.exists())

    def test_retry_after_is_honored_and_token_is_not_logged(self):
        backup_id, _ = self.backup(); enqueue_backup(self.conn, backup_id)
        item = self.conn.execute("SELECT id FROM telegram_backup_items").fetchone()[0]
        error = TelegramBackupError("TELEGRAM_RATE_LIMIT", "Limite de mensagens atingido.", transient=True, retry_after=123)
        status = process_item(self.conn, item, transport=FakeDocumentTransport(error), now=datetime(2026, 7, 13, tzinfo=timezone.utc))
        self.assertEqual("retry_wait", status)
        row = self.conn.execute("SELECT error_code,next_attempt_at,safe_log FROM telegram_backup_items WHERE id=?", (item,)).fetchone()
        self.assertEqual("TELEGRAM_RATE_LIMIT", row["error_code"]); self.assertEqual("2026-07-13 00:02:03", row["next_attempt_at"])
        self.assertNotIn("123456:", row["safe_log"])

    def test_definitive_permission_failure_does_not_change_local_backup(self):
        backup_id, target = self.backup(); enqueue_backup(self.conn, backup_id)
        item = self.conn.execute("SELECT id FROM telegram_backup_items").fetchone()[0]
        error = TelegramBackupError("TELEGRAM_FORBIDDEN", "Bot sem permissão no destino.", transient=False)
        self.assertEqual("failed", process_item(self.conn, item, transport=FakeDocumentTransport(error)))
        self.assertTrue(target.exists())
        backup = self.conn.execute("SELECT backup_status FROM backups WHERE id=?", (backup_id,)).fetchone()[0]
        self.assertEqual("available", backup)

    def test_server_failure_is_transient(self):
        backup_id, _ = self.backup(); enqueue_backup(self.conn, backup_id)
        item = self.conn.execute("SELECT id FROM telegram_backup_items").fetchone()[0]
        error = TelegramBackupError("TELEGRAM_HTTP_ERROR", "Falha HTTP ao enviar documento.", transient=True)
        self.assertEqual("retry_wait", process_item(self.conn, item, transport=FakeDocumentTransport(error)))

    def test_executive_uses_allowlist_and_does_not_show_internal_ids(self):
        now = datetime(2026, 7, 13, 15, tzinfo=timezone.utc); period = summary_period(self.conn, "executive", now)
        self.conn.execute("INSERT INTO audit_log(action,entity,entity_id,details,created_at) VALUES('login','user','1','{}','2026-07-12 12:00:00')")
        self.conn.execute("INSERT INTO audit_log(action,entity,entity_id,details,created_at) VALUES('equipment.created','equipment','7','{}','2026-07-12 13:00:00')")
        message = render_executive_telegram_summary(self.conn, period)
        self.assertIn("equipamento cadastrado", message); self.assertNotIn("equipment_id", message); self.assertNotIn("login", message)

    def test_web_mutations_have_rbac_and_csrf_gate(self):
        source = (ROOT / "backup_manager" / "app.py").read_text()
        section = source[source.index("def _telegram_form"):source.index("def handle_telegram_destination_create")]
        self.assertIn("require_admin", section); self.assertIn("require_operator", section)
        self.assertIn("valid_session_csrf", section)

    def test_zip_uses_temporary_and_removes_package(self):
        backup_id, target = self.backup(b"A" * 2000)
        self.conn.execute("UPDATE telegram_backup_policies SET compression_mode='zip' WHERE id=?", (self.policy,))
        enqueue_backup(self.conn, backup_id); item = self.conn.execute("SELECT id FROM telegram_backup_items").fetchone()[0]
        transport = FakeDocumentTransport(); self.assertEqual("sent", process_item(self.conn, item, transport=transport))
        sent_path = transport.calls[0][3]
        self.assertEqual(".zip", sent_path.suffix); self.assertFalse(sent_path.exists()); self.assertTrue(target.exists())

    def test_already_compressed_is_not_recompressed(self):
        backup_id, target = self.backup(b"fakezip", "config.zip")
        self.conn.execute("UPDATE telegram_backup_policies SET compression_mode='zip' WHERE id=?", (self.policy,))
        enqueue_backup(self.conn, backup_id); item = self.conn.execute("SELECT id FROM telegram_backup_items").fetchone()[0]
        transport = FakeDocumentTransport(); process_item(self.conn, item, transport=transport)
        self.assertEqual(target, transport.calls[0][3]); self.assertTrue(target.exists())

    def test_stats_are_separate_from_cloud(self):
        self.assertEqual(0, stats(self.conn)["sent"])


if __name__ == "__main__":
    unittest.main()
