from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from unittest import mock

from backup_manager.notifications import _ftp_notification, evaluate_conditions
from backup_manager.telegram_formatting import tg_escape


class TelegramOperationalVisualFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE notification_channels(
                id INTEGER PRIMARY KEY, channel_type TEXT, is_enabled INTEGER,
                timezone TEXT, maintenance_enabled INTEGER, maintenance_start TEXT,
                maintenance_end TEXT, cooldown_minutes INTEGER
            );
            INSERT INTO notification_channels VALUES(
                1, 'telegram', 1, 'America/Sao_Paulo', 0, '00:00', '00:00', 60
            );
            CREATE TABLE telegram_destinations(
                id INTEGER PRIMARY KEY, default_thread_id INTEGER, is_active INTEGER,
                deleted_at TEXT, use_for_alerts INTEGER
            );
            CREATE TABLE telegram_topic_mappings(
                id INTEGER PRIMARY KEY, destination_id INTEGER, thread_id INTEGER,
                is_active INTEGER, mapping_type TEXT, match_value TEXT
            );
            CREATE TABLE notification_states(
                dedup_key TEXT PRIMARY KEY, is_active INTEGER, last_seen_at TEXT,
                last_sent_at TEXT, recovered_at TEXT, updated_at TEXT
            );
            CREATE TABLE notification_queue(
                id INTEGER PRIMARY KEY, uuid TEXT, channel_id INTEGER, event_type TEXT,
                severity TEXT, subject TEXT, message TEXT, dedup_key TEXT, status TEXT,
                source_entity TEXT, source_entity_id TEXT, source_audit_id INTEGER,
                next_attempt_at TEXT, destination_id INTEGER, thread_id INTEGER
            );
            CREATE TABLE notification_history(
                id INTEGER PRIMARY KEY, queue_id INTEGER, channel_type TEXT,
                event_type TEXT, status TEXT, attempt INTEGER, error_code TEXT
            );
            CREATE TABLE equipment_groups(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE equipment(
                id INTEGER PRIMARY KEY, hostname TEXT, group_id INTEGER
            );
            CREATE TABLE backups(
                id INTEGER PRIMARY KEY, uuid TEXT, original_filename TEXT,
                equipment_id INTEGER
            );
        """)
        self.channel = self.conn.execute(
            "SELECT * FROM notification_channels WHERE channel_type='telegram'"
        ).fetchone()

    def test_ftp_group_is_between_equipment_and_file_and_absence_adds_no_blank_line(self) -> None:
        self.conn.execute("INSERT INTO equipment_groups VALUES(1, 'Acesso & Borda')")
        self.conn.execute("INSERT INTO equipment VALUES(1, 'CE-BASE-<CONECTA>', 1)")
        self.conn.execute("INSERT INTO backups VALUES(1, 'with-group', 'router.backup', 1)")
        row = {"id": 10, "created_at": "2026-07-20 14:19:00"}
        subject, message, _ = _ftp_notification(
            self.conn, row, {"backup_uuid": "with-group", "filename": "router.backup"}, self.channel
        )
        self.assertEqual("Backup recebido por FTP", subject)
        self.assertEqual(
            "🖥️ Equipamento: CE-BASE-<CONECTA>\n"
            "🏷️ Grupo: Acesso & Borda\n"
            "📄 Arquivo: Backup de configuração\n"
            "🕒 Horário: 20/07/2026 11:19",
            message,
        )
        self.assertIn("&lt;CONECTA&gt;", tg_escape(message))
        self.assertIn("Acesso &amp; Borda", tg_escape(message))

        self.conn.execute("INSERT INTO equipment VALUES(2, 'SEM-GRUPO', NULL)")
        self.conn.execute("INSERT INTO backups VALUES(2, 'without-group', 'router.rsc', 2)")
        _, without_group, _ = _ftp_notification(
            self.conn, row, {"backup_uuid": "without-group", "filename": "router.rsc"}, self.channel
        )
        self.assertEqual(
            "🖥️ Equipamento: SEM-GRUPO\n"
            "📄 Arquivo: Backup de configuração\n"
            "🕒 Horário: 20/07/2026 11:19",
            without_group,
        )
        self.assertNotIn("Grupo:", without_group)
        self.assertNotIn("\n\n", without_group)

    def test_backup_and_rsc_artifacts_hide_names_but_keep_distinct_keys(self) -> None:
        self.conn.execute("INSERT INTO equipment VALUES(1, 'ROTEADOR', NULL)")
        self.conn.execute("INSERT INTO backups VALUES(1, 'artifact-backup', 'router.backup', 1)")
        self.conn.execute("INSERT INTO backups VALUES(2, 'artifact-rsc', 'router.rsc', 1)")
        row = {"id": 11, "created_at": "2026-07-20 14:19:00"}
        rendered = [
            _ftp_notification(self.conn, row, {"backup_uuid": identifier}, self.channel)
            for identifier in ("artifact-backup", "artifact-rsc")
        ]
        self.assertEqual(rendered[0][1], rendered[1][1])
        self.assertIn("Arquivo: Backup de configuração", rendered[0][1])
        self.assertNotIn("router.", rendered[0][1])
        self.assertNotEqual(rendered[0][2], rendered[1][2])

    def test_late_backup_recovery_is_structured_real_time_private_and_deduplicated(self) -> None:
        recovery_time = datetime(2026, 7, 20, 14, 20, tzinfo=timezone.utc)
        late = {"equipment": [{
            "id": 77, "hostname": "CE-BASE-<CONECTA>",
            "last_backup": "2026-07-18 14:00:00", "age_hours": 48.0,
        }], "services": [], "storage": {"percent": 0}}
        normal = {"equipment": [{
            "id": 77, "hostname": "CE-BASE-<CONECTA>",
            "last_backup": "2026-07-20 14:19:00", "age_hours": 1 / 60,
        }], "services": [], "storage": {"percent": 0}}
        with mock.patch("backup_manager.notifications.dashboard_snapshot", return_value=late):
            self.assertEqual(1, evaluate_conditions(self.conn, recovery_time))
            self.assertEqual(0, evaluate_conditions(self.conn, recovery_time))
        with mock.patch("backup_manager.notifications.dashboard_snapshot", return_value=normal):
            self.assertEqual(1, evaluate_conditions(self.conn, recovery_time))

        recovery = self.conn.execute(
            "SELECT subject,message FROM notification_queue WHERE event_type='backup_late_recovery'"
        ).fetchone()
        self.assertEqual("Backup normalizado", recovery["subject"])
        self.assertEqual(
            "🖥️ Equipamento: CE-BASE-<CONECTA>\n"
            "✅ Voltou a receber backups dentro do prazo\n"
            "🕒 Horário: 20/07/2026 11:20",
            recovery["message"],
        )
        visible = f"{recovery['subject']}\n{recovery['message']}"
        for forbidden in ("UUID", "entity", "lifecycle", "event_type", "Identificador", "77"):
            self.assertNotIn(forbidden, visible)
        self.assertIn("CE-BASE-&lt;CONECTA&gt;", tg_escape(visible))
        self.assertEqual(
            2,
            self.conn.execute(
                "SELECT COUNT(*) FROM notification_queue WHERE dedup_key='equipment:77:late'"
            ).fetchone()[0],
        )


if __name__ == "__main__":
    unittest.main()
