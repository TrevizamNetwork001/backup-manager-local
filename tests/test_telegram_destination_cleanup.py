import sqlite3
import unittest
from datetime import datetime, timezone
import uuid
from pathlib import Path

from backup_manager.telegram_backup import create_destination, render_telegram_backup_caption, render_telegram_backup_failure, render_telegram_backup_too_large
from backup_manager.telegram_destinations import cancel_pending, disable_destination, pending_counts

ROOT = Path(__file__).resolve().parents[1]

class TelegramDestinationCleanupTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(":memory:"); self.conn.row_factory=sqlite3.Row; self.conn.execute("PRAGMA foreign_keys=ON")
        for migration in sorted((ROOT/"migrations").glob("*.sql")): self.conn.executescript(migration.read_text())
        self.destination=self.conn.execute("INSERT INTO telegram_destinations(uuid,name,chat_id,is_active) VALUES(?, 'Antigo','-100123',1)",(str(uuid.uuid4()),)).lastrowid
        self.uuid=self.conn.execute("SELECT uuid FROM telegram_destinations WHERE id=?",(self.destination,)).fetchone()[0]

    def tearDown(self): self.conn.close()

    def test_duplicate_active_name_is_rejected_but_inactive_is_allowed(self):
        with self.assertRaises((ValueError,sqlite3.IntegrityError)): create_destination(self.conn,name=" antigo ",chat_id="-100456")
        self.conn.execute("UPDATE telegram_destinations SET is_active=0 WHERE id=?",(self.destination,))
        self.assertIsInstance(create_destination(self.conn,name="Antigo",chat_id="-100456"),int)

    def test_disable_preserves_history_and_cancel_requires_type(self):
        channel=self.conn.execute("SELECT id FROM notification_channels WHERE channel_type='telegram'").fetchone()[0]
        self.conn.execute("INSERT INTO notification_queue(uuid,channel_id,event_type,severity,subject,message,dedup_key,destination_id) VALUES(?,?,'test','info','s','m',?,?)",(str(uuid.uuid4()),channel,str(uuid.uuid4()),self.destination))
        self.assertEqual(1,pending_counts(self.conn,self.destination)["alert"])
        disable_destination(self.conn,self.uuid)
        self.assertEqual(1,cancel_pending(self.conn,self.uuid,"alert"))
        self.assertEqual("suppressed",self.conn.execute("SELECT status FROM notification_queue").fetchone()[0])
        self.assertEqual(2,self.conn.execute("SELECT COUNT(*) FROM telegram_destination_actions").fetchone()[0])

    def test_caption_uses_configured_timezone_and_handles_date_boundary(self):
        stamp = datetime(2026, 9, 13, 1, 8, tzinfo=timezone.utc)
        caption = render_telegram_backup_caption({}, "backup.cfg", 1, now=stamp,
                                                  timezone_name="America/Sao_Paulo")
        self.assertIn("Enviado em: 12/09/2026 22:08", caption)
        caption = render_telegram_backup_caption({}, "backup.cfg", 1, now=stamp,
                                                  timezone_name="UTC")
        self.assertIn("Enviado em: 13/09/2026 01:08", caption)

    def test_premium_caption_escapes_and_omits_missing(self):
        caption=render_telegram_backup_caption(
            {"hostname":"OLT <Centro>","group_name":"Laboratório & QA","vendor":"ignorado",
             "source_method":"ftp","local_sha256":"a"*64},
            "x&y.zip",1024,now=datetime(2026,7,16,10,55,tzinfo=timezone.utc))
        self.assertEqual(
            "✅ Backup concluído\n"
            "🏷️ Grupo: Laboratório &amp; QA\n"
            "🖥️ Equipamento: OLT &lt;Centro&gt;\n"
            "📄 Arquivo: x&amp;y.zip\n"
            "🕒 Enviado em: 16/07/2026 07:55",
            caption,
        )
        for forbidden in ("SHA-256", "Método", "Fabricante", "v1.1.0", "Enviando"):
            self.assertNotIn(forbidden, caption)

        without_group=render_telegram_backup_caption(
            {"hostname":"Equipamento de teste","vendor":"qualquer"},
            "backup-laboratorio.txt",69,now=datetime(2026,7,16,10,55,tzinfo=timezone.utc))
        self.assertEqual(4, len(without_group.splitlines()))
        self.assertNotIn("Grupo:", without_group)
        self.assertNotIn("None",caption)
        self.assertIn("Cópia não enviada",render_telegram_backup_failure({"hostname":"OLT"},"x.zip",1))
        self.assertIn("excede o limite",render_telegram_backup_too_large({"hostname":"OLT"},"x.zip",1))

if __name__ == "__main__": unittest.main()
