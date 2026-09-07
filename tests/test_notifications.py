import pathlib
import sqlite3
import unittest

from backup_manager.notifications import format_elapsed_hours, scan_audit_events


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "backup_manager/app.py").read_text()
NOTIFICATIONS = (ROOT / "backup_manager/notifications.py").read_text()
WORKER = (ROOT / "backup_manager/worker.py").read_text()


class NotificationArchitectureTests(unittest.TestCase):
    def test_audit_notification_uses_configured_brazilian_timezone(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE notification_cursors(source TEXT PRIMARY KEY,last_id INTEGER,updated_at TEXT);
            INSERT INTO notification_cursors VALUES('audit_log',0,NULL);
            CREATE TABLE audit_log(id INTEGER PRIMARY KEY,user_id INTEGER,action TEXT,entity TEXT,
                entity_id TEXT,details TEXT,ip_address TEXT,created_at TEXT);
            INSERT INTO audit_log VALUES(1,NULL,'backup.created_from_ssh','backup','abc','{}','',
                '2026-07-17 04:20:59');
            CREATE TABLE notification_channels(id INTEGER PRIMARY KEY,channel_type TEXT,is_enabled INTEGER,
                timezone TEXT,maintenance_enabled INTEGER,maintenance_start TEXT,maintenance_end TEXT,
                cooldown_minutes INTEGER);
            INSERT INTO notification_channels VALUES(1,'telegram',0,'America/Sao_Paulo',0,'00:00','00:00',60);
            CREATE TABLE telegram_destinations(id INTEGER PRIMARY KEY,default_thread_id INTEGER,is_active INTEGER,
                deleted_at TEXT,use_for_alerts INTEGER);
            CREATE TABLE telegram_topic_mappings(id INTEGER PRIMARY KEY,destination_id INTEGER,thread_id INTEGER,
                is_active INTEGER,mapping_type TEXT,match_value TEXT);
            CREATE TABLE notification_queue(id INTEGER PRIMARY KEY,uuid TEXT,channel_id INTEGER,event_type TEXT,
                severity TEXT,subject TEXT,message TEXT,dedup_key TEXT,status TEXT,source_entity TEXT,
                source_entity_id TEXT,source_audit_id INTEGER,next_attempt_at TEXT,destination_id INTEGER,thread_id INTEGER);
            CREATE TABLE notification_history(id INTEGER PRIMARY KEY,queue_id INTEGER,channel_type TEXT,
                event_type TEXT,status TEXT,attempt INTEGER,error_code TEXT);
        """)
        self.assertEqual((1, 1), scan_audit_events(conn))
        message = conn.execute("SELECT message FROM notification_queue").fetchone()[0]
        self.assertIn("🕒 Horário: 17/07/2026 01:20", message)
        self.assertNotIn("UTC", message)
        for technical in ("Entidade", "Identificador", "abc", "backup.created_from_ssh"):
            self.assertNotIn(technical, message)

    def test_elapsed_time_uses_seconds_minutes_hours_and_days(self):
        self.assertEqual("18 segundos", format_elapsed_hours(0.005))
        self.assertEqual("30 minutos", format_elapsed_hours(0.5))
        self.assertEqual("12 horas", format_elapsed_hours(12.5))
        self.assertEqual("2 dias e 10 horas", format_elapsed_hours(58.5))
        self.assertEqual("tempo indisponível", format_elapsed_hours(None))

    def test_web_request_never_calls_telegram_or_network(self):
        page = APP[APP.index("def notifications_page"):APP.index("def parse_days")]
        for forbidden in ("urlopen", "TelegramTransport", "subprocess", "requests.", "api.telegram.org"):
            self.assertNotIn(forbidden, page)
        self.assertIn("queue_basic_test", page)

    def test_worker_owns_delivery_and_transport_has_bounded_timeout(self):
        self.assertIn("run_cycle(conn)", WORKER)
        self.assertIn("urllib.request.urlopen(request, timeout=10)", NOTIFICATIONS)
        self.assertNotIn("shell=True", NOTIFICATIONS)

    def test_token_is_encrypted_and_delivery_errors_are_sanitized(self):
        self.assertIn("encrypt_secret(token)", NOTIFICATIONS)
        self.assertIn('decrypt_secret(row["token_encrypted"])', NOTIFICATIONS)
        self.assertIn("raise TelegramAPIError", NOTIFICATIONS)
        self.assertNotIn("str(exc)", NOTIFICATIONS)

    def test_notification_ui_is_collapsed_translated_and_responsive(self):
        page = (ROOT / "backup_manager/notifications_page_views.py").read_text()
        css = (ROOT / "static/app.css").read_text()
        javascript = (ROOT / "static/app.js").read_text()
        self.assertIn('<details id="telegram-config"', page)
        self.assertIn('type="password"', page)
        self.assertIn("Dia da semana<select", page)
        self.assertNotIn("Dia semanal (0=segunda)", page)
        self.assertIn("Configurações avançadas", page)
        self.assertIn("data-maintenance-times", page)
        self.assertIn("notification-preview", page)
        self.assertIn("Aguardando nova tentativa", page)
        self.assertIn("Último envio bem-sucedido", page)
        self.assertIn("Última falha", page)
        self.assertIn("notification-integration-overview", page)
        self.assertIn("Identificado após o teste", page)
        self.assertIn("Atividade recente", page)
        self.assertIn("<svg", page)
        self.assertIn("notification-summary-grid", page)
        self.assertIn("notification-summary-icon", page)
        self.assertIn("Prévia das mensagens", page)
        self.assertNotIn("Testar diário", page)
        self.assertIn("notification-history-filters", page)
        self.assertIn("data-history-more", page)
        self.assertIn("Falha na entrega pelo Telegram", page)
        self.assertIn("Mostrar mais", javascript)
        self.assertIn("notification-editor-section", page)
        self.assertIn("notification-switch", page)
        self.assertIn("Bot e destino", page)
        self.assertIn("Regras de entrega", page)
        self.assertIn("notification-editor-actions", page)
        self.assertIn("data-secret-toggle", page)
        self.assertIn("notification-secret-input", page)
        self.assertIn("Ocultar token", javascript)
        self.assertIn("notification-advanced-grid", page)
        self.assertIn("Roteamento das mensagens", page)
        self.assertIn("Diagnóstico do canal", page)
        self.assertIn("Resultado do último teste", page)
        self.assertIn("notification-preview-device", page)
        self.assertIn("notification-preview-message", page)
        self.assertIn("Conteúdo ilustrativo", page)
        self.assertNotIn("13/07/2026", page)
        self.assertIn(".notification-preview-message[hidden] { display: none; }", css)
        self.assertIn("@media (max-width: 520px)", css)
        self.assertIn("pollNotificationTest", javascript)

    def test_notification_settings_and_tests_require_csrf_and_admin(self):
        page = APP[APP.index("def handle_notifications_save"):APP.index("def parse_days")]
        self.assertGreaterEqual(page.count("_valid_settings_csrf"), 3)
        self.assertGreaterEqual(page.count("require_admin"), 4)


if __name__ == "__main__":
    unittest.main()
